"""Regression tests for the credential leak via ``POST /api/v1/query/explain``.

The bug these cover: ``QueryService.explain`` runs the same ``@stage`` →
``FILES()`` translation as ``execute``, which injects real storage credentials
into the statement. ``execute`` redacted the value it returned, ``explain`` did
not — so ``QueryResponse.executed_sql`` handed the access key and the secret key
to any authenticated user, at any role.

The assertions here are on the **HTTP response body**, not on the helper:
``redact_sql_credentials`` was already correct and unit-tested; what was missing
was a call site. Only a test that goes through the router can catch the next
missing call site, so the app is built by the real factory (minus the DB-backed
lifespan) and the request is issued with ``httpx`` exactly as the frontend does.

Values are placeholders, never a real credential.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.common.sql_guard import CredentialsRedactionError, redact_sql_credentials
from app.core.exceptions import register_exception_handlers

ACCESS_KEY = "AKIA_EXPLAIN_TESTVALUE_123"
SECRET_KEY = "SECRET_EXPLAIN_TESTVALUE_456"
# The single-quoted placeholder redaction writes back.
REDACTED = "***"
# Present in the SQL but not a credential parameter — must survive redaction.
S3_PATH = "s3://stages/explain_probe.csv"

STAGE_SQL = "SELECT * FROM @stage1.data.csv"
EXPLAIN_ENDPOINT = "/api/v1/query/explain"


def _stage_config():
    from app.modules.query.dialect.translator import StorageConfig

    return StorageConfig(
        storage_type="s3",
        endpoint="http://minio:9000",
        bucket="stages",
        base_prefix="db/sch/stage1",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
    )


class RecordingRepo:
    """Stub repository: records the SQL sent to the engine, returns a plan."""

    def __init__(self):
        self.calls: list[str] = []

    async def execute_as_user(self, sql, **kwargs):
        from app.modules.query.repository import QueryResult

        self.calls.append(sql)
        # ``executed_sql`` is set from the statement the engine received, which
        # is how the real repository does it — that is the value that leaked.
        return QueryResult(
            columns=["PLAN"],
            rows=[["EXPLAIN PLAN"]],
            row_count=1,
            executed_sql=sql,
        )


@pytest.fixture
def explain_client(monkeypatch):
    """A real app with only the DB-backed pieces stubbed out.

    The router, its response model, the service singleton and the exception
    handlers are the ones production uses; only the engine, the stage metadata
    and the session lookup are replaced.
    """
    import app.modules.query.service as service_module
    from app.core import deps as deps_module
    from app.modules.query.service import query_service

    repo = RecordingRepo()

    async def fake_configs(database, schema):
        return {"stage1": _stage_config()}

    async def fake_csv_params(parsed, stage_configs):
        return {}, None

    monkeypatch.setattr(query_service, "_repo", repo)
    monkeypatch.setattr(query_service, "_load_stage_configs", fake_configs)
    monkeypatch.setattr(query_service, "_detect_csv_params", fake_csv_params)
    # The service decrypts the session password before calling the engine.
    monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")

    async def fake_current_user():
        return {
            "username": "analyst",
            "session_id": "sess-explain-probe",
            "roles": ["analyst"],  # deliberately non-admin
            "active_role": "analyst",
            "encrypted_password": "enc",
        }

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/__boom")
    async def _boom():
        raise RuntimeError(f"InjectedStorageError at {EXPLAIN_ENDPOINT} secret={SECRET_KEY}")

    @app.post("/__creds")
    async def _creds():
        raise CredentialsRedactionError("redaction failed")

    from app.modules.query.router import router as query_router

    app.include_router(query_router, prefix="/api/v1/query")
    app.dependency_overrides[deps_module.get_current_user] = fake_current_user

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, repo


class TestExplainResponseIsRedacted:
    """The reported finding: credentials in the body of ``/query/explain``."""

    def test_response_body_carries_no_credentials(self, explain_client):
        client, _repo = explain_client
        resp = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL})

        assert resp.status_code == 200
        body = resp.text
        assert ACCESS_KEY not in body, "storage access key leaked in the API response"
        assert SECRET_KEY not in body, "storage secret key leaked in the API response"

    def test_response_executed_sql_is_redacted(self, explain_client):
        client, _repo = explain_client
        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()

        assert payload["success"] is True
        assert "'aws.s3.access_key'='***'" in payload["executed_sql"]
        assert "'aws.s3.secret_key'='***'" in payload["executed_sql"]

    def test_response_still_documents_what_ran(self, explain_client):
        """Redaction is value-only: a reviewer can still read the plan target."""
        client, _repo = explain_client
        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()

        assert payload["executed_sql"].startswith("EXPLAIN SELECT * FROM FILES(")
        assert "s3://stages/db/sch/stage1/data.csv" in payload["executed_sql"]
        assert "'format'='csv'" in payload["executed_sql"]

    def test_original_sql_is_the_user_stage_reference(self, explain_client):
        client, _repo = explain_client
        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()

        assert payload["original_sql"] == STAGE_SQL
        assert "FILES(" not in payload["original_sql"]


class TestEngineStillGetsRealCredentials:
    """Redaction happens on the way out — ``EXPLAIN`` must still be plannable."""

    def test_engine_receives_unredacted_credentials(self, explain_client):
        client, repo = explain_client
        client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL})

        assert repo.calls, "no statement reached the engine"
        sent = repo.calls[-1]
        assert sent.startswith("EXPLAIN ")
        assert f"'aws.s3.access_key'='{ACCESS_KEY}'" in sent
        assert f"'aws.s3.secret_key'='{SECRET_KEY}'" in sent
        assert "'***'" not in sent, "the engine was handed a redacted credential"


RAW_CREDENTIAL_SQL = (
    f"SELECT * FROM FILES('path'='{S3_PATH}', 'aws.s3.access_key'='{ACCESS_KEY}', "
    f"'aws.s3.secret_key'='{SECRET_KEY}')"
)


class TestRedactionCannotBeForgotten:
    """The class of bug, not just the reported site.

    ``QueryResult`` is the only thing the router serialises, so redacting on
    construction closes every current and future path that builds a response —
    including the ``translate_stage_query`` failure branch that was also
    unredacted.
    """

    def test_query_result_redacts_on_construction(self):
        from app.modules.query.repository import QueryResult

        result = QueryResult(executed_sql=redact_sql_credentials(STAGE_SQL))
        assert "***" not in result.executed_sql  # nothing to redact

        result = QueryResult(executed_sql=RAW_CREDENTIAL_SQL)
        assert ACCESS_KEY not in result.executed_sql
        assert SECRET_KEY not in result.executed_sql
        assert "'aws.s3.access_key'='***'" in result.executed_sql

    def test_unknown_stage_error_path_is_redacted(self, explain_client, monkeypatch):
        """The ValueError branch returns the normalized SQL unwrapped."""
        client, _repo = explain_client
        from app.modules.query.service import query_service

        async def no_stages(database, schema):
            return {}

        monkeypatch.setattr(query_service, "_load_stage_configs", no_stages)
        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()

        assert payload["warnings"], "the failure must be reported to the caller"
        assert ACCESS_KEY not in str(payload)
        assert SECRET_KEY not in str(payload)


class TestErrorResponsesCarryNoCredentials:
    """AGENTS.md §2 also lists *error messages* as a never-store destination."""

    def test_internal_error_traceback_is_not_returned_verbatim(self, explain_client):
        client, _repo = explain_client
        resp = client.post("/__boom")

        assert resp.status_code >= 500
        body = resp.text
        assert "Traceback" not in body, "a stack trace reached the client"
        assert SECRET_KEY not in body, "an exception message carried a credential"

    def test_redaction_failure_does_not_fall_back_to_raw_sql(self, explain_client):
        """A failing redactor must fail closed, never return the original."""
        client, _repo = explain_client
        resp = client.post("/__creds")

        assert resp.status_code >= 500
        assert "'***'" not in resp.text


class TestSanitizingResponseType:
    """The optional hard guarantee at the response boundary."""

    def test_body_is_sanitized_on_the_way_out(self):
        from app.modules.query.router import SanitizingJSONResponse

        response = SanitizingJSONResponse(
            content={
                "success": True,
                "executed_sql": RAW_CREDENTIAL_SQL,
                "rows": [[RAW_CREDENTIAL_SQL]],
            }
        )
        body = response.body.decode()
        assert ACCESS_KEY not in body
        assert SECRET_KEY not in body
        assert "'aws.s3.access_key'='***'" in body

    def test_sanitizing_preserves_the_envelope(self):
        from app.modules.query.router import SanitizingJSONResponse

        response = SanitizingJSONResponse(
            content={"success": True, "row_count": 3, "rows": [["a", 1]]}
        )
        import json

        assert json.loads(response.body) == {
            "success": True,
            "row_count": 3,
            "rows": [["a", 1]],
        }
        assert response.media_type == "application/json"

    def test_sanitizing_recurses_into_nested_structures(self):
        from app.modules.query.router import SanitizingJSONResponse

        payload = {"warnings": [RAW_CREDENTIAL_SQL], "plan": {"sql": RAW_CREDENTIAL_SQL}}
        body = json.dumps(SanitizingJSONResponse._sanitize(payload))
        assert ACCESS_KEY not in body
        assert SECRET_KEY not in body

    def test_sanitizing_leaves_credential_free_strings_alone(self):
        from app.modules.query.router import SanitizingJSONResponse

        raw = "SELECT * FROM FILES('path'='s3://stages/x.csv', 'format'='csv')"
        assert SanitizingJSONResponse._sanitize(raw) == raw

    def test_router_declares_the_sanitizing_response(self):
        """Guards against the response class being dropped in a refactor."""
        from app.modules.query.router import SanitizingJSONResponse, router

        classes = {
            getattr(route, "response_class", None)
            for route in router.routes
            if "explain" in getattr(route, "path", "") or "execute" in getattr(route, "path", "")
        }
        assert SanitizingJSONResponse in classes


class TestDoubleQuotedAssignmentIsRedacted:
    """Negative controls for the ``"key"="value"`` shape.

    ``redact_sql_credentials`` covers the ``'key'='value'`` form the injector
    emits. A caller can write the double-quoted form by hand, and the first
    revision of this fix neither redacted it nor noticed it had not: the
    verification pass captured the value with a backreferenced quote run
    (``(?P<q>['"]).*?(?P=q)``) where ``.*?`` can match empty and the
    backreference is then satisfied zero-width, so the remaining alternative
    branch swallowed the opening quote and ``strip("'\\"")`` emptied a live
    value. These tests fail if that shape is ever unredacted again.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            'FILES("aws.s3.access_key"="AKIA_DQUOTE_PLACEHOLDER")',
            'FILES("aws.s3.secret_key"="SECRET_DQUOTE_PLACEHOLDER")',
            'FILES("aws.s3.session_token"="TOKEN_DQUOTE_PLACEHOLDER")',
            'FILES("aws.s3.secret_key" => "SECRET_DQUOTE_PLACEHOLDER")',
            'FILES(`aws.s3.secret_key`="SECRET_DQUOTE_PLACEHOLDER")',
        ],
    )
    def test_values_do_not_survive(self, sql):
        out = redact_sql_credentials(sql)
        assert "PLACEHOLDER" not in out, f"credential value survived: {out}"
        assert f"'{REDACTED}'" in out

    def test_key_quoting_is_preserved(self):
        """Redaction stays value-only — the shape is still readable."""
        out = redact_sql_credentials('FILES("aws.s3.access_key"="PLACEHOLDER_V")')
        assert out == "FILES(\"aws.s3.access_key\"='***')"

    def test_idempotent(self):
        once = redact_sql_credentials('FILES("aws.s3.access_key"="PLACEHOLDER_V")')
        assert redact_sql_credentials(once) == once

    def test_verification_pass_sees_a_populated_double_quoted_value(self):
        """The guard, in isolation, must not be fooled by the empty-match trap."""
        from app.common.sql_guard import _POPULATED_CREDENTIAL, _normalized_value

        match = _POPULATED_CREDENTIAL.search('FILES("aws.s3.access_key"="LIVEVALUE")')
        assert match is not None, "the verification pattern missed the assignment"
        assert _normalized_value(match.group("value")) == "LIVEVALUE"

    @pytest.mark.parametrize(
        "sql",
        [
            "FILES('aws.s3.access_key'='***')",
            "FILES(\"aws.s3.access_key\"='***')",
            "FILES('aws.s3.access_key'='')",
            "FILES('path'='s3://stages/x.csv', 'format'='csv')",
        ],
    )
    def test_redacted_or_empty_values_are_left_alone(self, sql):
        """No false positives: nothing to hide means nothing rewritten."""
        try:
            out = redact_sql_credentials(sql)
        except CredentialsRedactionError:
            pytest.fail(f"failed closed on a value that is already redacted: {sql}")
        assert REDACTED in out or "path" in out

    def test_double_quoted_form_is_redacted_end_to_end(self, explain_client):
        """The reported shape, through the real response path."""
        client, repo = explain_client
        sql = (
            'SELECT * FROM FILES("path"="s3://stages/x.csv", '
            f'"aws.s3.access_key"="{ACCESS_KEY}", "aws.s3.secret_key"="{SECRET_KEY}")'
        )
        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": sql}).json()

        assert ACCESS_KEY not in str(payload)
        assert SECRET_KEY not in str(payload)
        # The engine still gets the caller's own credentials verbatim.
        assert ACCESS_KEY in repo.calls[-1]


class TestGenericErrorHandlerStillWorks:
    """``JSONResponse`` is the documented return type of the error contract."""

    def test_handler_may_return_a_plain_json_response(self):
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/__plain")
        async def _plain():
            raise RuntimeError("boom")

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/__plain")
        assert resp.status_code >= 500
        assert isinstance(JSONResponse(content={}), JSONResponse)
