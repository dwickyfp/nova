"""Regression tests for the credential leak via ``NOVA_SYSTEM.AUDIT_LOG``.

The bug these cover: ``QueryService.execute`` injected real storage credentials
into the SQL sent to StarRocks, then handed that same credential-bearing string
to ``write_audit_log`` (both the SUCCESS and the ERROR path) and to
``QueryResult.executed_sql``. The API's query-history endpoint does not select
``rewritten_sql``, so the only way to observe the leak was to read the audit
table — which is why the pre-existing credential tests never caught it.

The assertions here are on the *destination*: whatever ``write_audit_log`` would
persist, and whatever the response carries. Values are placeholders
(``AKIA_TESTVALUE_123``) — never a real credential.
"""

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService

# Placeholders — these must never reach an audit row or an API response.
ACCESS_KEY = "AKIA_TESTVALUE_123"
SECRET_KEY = "SECRET_TESTVALUE_456"
SESSION_TOKEN = "SESSION_TESTVALUE_789"

CREDENTIAL_PARAMS = {
    "aws.s3.access_key": ACCESS_KEY,
    "aws.s3.secret_key": SECRET_KEY,
    "aws.s3.session_token": SESSION_TOKEN,
}


def _credential_sql() -> str:
    """SQL shaped exactly like the translator's FILES() output."""
    params = ", ".join(f"'{k}'='{v}'" for k, v in CREDENTIAL_PARAMS.items())
    return (
        "SELECT * FROM FILES('path'='s3://stages/x.csv', 'format'='csv', "
        f"{params})"
    )


class RecordingRepo:
    """Records the SQL the engine would receive, then succeeds or raises."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self._error = error

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        if self._error is not None:
            raise self._error
        return QueryResult(executed_sql=sql, columns=["v"], rows=[[1]], row_count=1)


class AuditSink:
    """Captures every ``write_audit_log`` call instead of writing to StarRocks."""

    def __init__(self):
        self.entries: list[dict] = []

    async def __call__(self, **kwargs):
        self.entries.append(kwargs)
        return "query-id"

    @property
    def persisted_sql(self) -> list[str]:
        return [
            entry["rewritten_sql"]
            for entry in self.entries
            if entry.get("rewritten_sql") is not None
        ]


@pytest.fixture
def wired(monkeypatch):
    """Install a recording repo + audit sink, and required config stubs."""

    def make(error: Exception | None = None):
        sink = AuditSink()
        repo = RecordingRepo(error=error)
        svc = QueryService()
        svc._repo = repo
        monkeypatch.setattr("app.modules.query.service.write_audit_log", sink)
        monkeypatch.setattr(
            "app.modules.query.service.decrypt_password", lambda value: "pw"
        )
        # Credential injection now lives in the shared SQL pipeline, so the
        # patch target moved with it (NOVA-28 PR 3).
        monkeypatch.setattr(
            "app.modules.query.sql_pipeline.get_credential_params",
            lambda *args, **kwargs: dict(CREDENTIAL_PARAMS),
        )
        return svc, repo, sink

    return make


@pytest.fixture
def redact():
    """The redaction helper, resolved lazily so a pre-fix run still collects."""
    from app.common.sql_guard import redact_sql_credentials

    return redact_sql_credentials


async def _execute_stage_query(svc, **kwargs):
    """Run a hand-built FILES()-with-credentials statement through the service.

    The SQL already contains the FILES() call, so no stage config or engine is
    needed: the pipeline's injection step is a no-op (credentials are already
    present) and the statement goes straight to the recording repo.
    """
    return await svc.execute(
        sql=_credential_sql(),
        username="analyst",
        encrypted_password="enc",
        **kwargs,
    )


class TestAuditRowIsRedacted:
    """Criterion 2 + 4: no credential value may reach the persisted rewrite."""

    async def test_success_path_audit_has_no_credentials(self, wired):
        svc, _repo, sink = wired()
        await _execute_stage_query(svc)

        assert sink.persisted_sql, "no audit row was written"
        for stored in sink.persisted_sql:
            for secret in CREDENTIAL_PARAMS.values():
                assert secret not in stored, f"{secret!r} persisted to AUDIT_LOG"

    async def test_success_path_audit_keeps_redaction_marker(self, wired):
        svc, _repo, sink = wired()
        await _execute_stage_query(svc)

        stored = sink.persisted_sql[-1]
        assert "'aws.s3.access_key'='***'" in stored
        assert "'aws.s3.secret_key'='***'" in stored
        assert "'aws.s3.session_token'='***'" in stored

    async def test_error_path_audit_has_no_credentials(self, wired):
        """The ERROR branch is a separate call site and leaked independently."""
        svc, _repo, sink = wired(error=RuntimeError("engine exploded"))
        with pytest.raises(RuntimeError):
            await _execute_stage_query(svc)

        assert sink.entries, "the error path wrote no audit row"
        assert sink.entries[-1]["status"] == "ERROR"
        for stored in sink.persisted_sql:
            for secret in CREDENTIAL_PARAMS.values():
                assert secret not in stored, f"{secret!r} persisted on ERROR path"

    async def test_audit_row_still_documents_what_ran(self, wired):
        """Redaction must be value-only — the row stays forensically useful."""
        svc, _repo, sink = wired()
        await _execute_stage_query(svc)

        stored = sink.persisted_sql[-1]
        assert "s3://stages/x.csv" in stored
        assert "'format'='csv'" in stored
        assert stored.startswith("SELECT * FROM FILES(")


class TestEngineStillGetsRealCredentials:
    """Criterion 1 + 5: redaction must not break @stage execution."""

    async def test_engine_receives_unredacted_credentials(self, wired):
        svc, repo, _sink = wired()
        await _execute_stage_query(svc)

        assert repo.calls, "the statement never reached the engine"
        sent = repo.calls[-1]
        for key, secret in CREDENTIAL_PARAMS.items():
            assert f"'{key}'='{secret}'" in sent, f"engine lost {key}"

    async def test_engine_sql_is_not_the_redacted_form(self, wired):
        svc, repo, _sink = wired()
        await _execute_stage_query(svc)

        assert "'***'" not in repo.calls[-1]


class TestResponseIsRedacted:
    """Criterion 3: ``QueryResponse.executed_sql`` is the other exfil path."""

    async def test_executed_sql_exposes_no_credentials(self, wired):
        svc, _repo, _sink = wired()
        result = await _execute_stage_query(svc)

        for secret in CREDENTIAL_PARAMS.values():
            assert secret not in result.executed_sql, "credential leaked in response"

    async def test_executed_sql_keeps_credential_param_names(self, wired):
        """The frontend still shows which parameters were injected."""
        svc, _repo, _sink = wired()
        result = await _execute_stage_query(svc)

        assert "aws.s3.access_key" in result.executed_sql
        assert "'aws.s3.access_key'='***'" in result.executed_sql

    async def test_original_sql_redacts_explicit_credentials(self, wired):
        """Direct SQL can contain secrets too; both response fields must redact."""
        svc, _repo, _sink = wired()
        result = await _execute_stage_query(svc)

        for secret in CREDENTIAL_PARAMS.values():
            assert secret not in result.original_sql
        assert "'aws.s3.access_key'='***'" in result.original_sql


class TestStagePipelineEndToEnd:
    """The real ``@stage`` flow: user types ``@stage1.data.csv``, Nova injects.

    Criterion 3 asked explicitly whether ``QueryResponse.executed_sql`` carries
    credentials to the frontend. It did, and it was the *only* response field
    that did: ``original_sql`` is the user's own ``@stage`` text, which never
    contains an injected credential. Both are asserted below so a future change
    that starts writing the rewritten SQL into ``original_sql`` is caught.
    """

    @staticmethod
    def _storage_config():
        from app.modules.query.dialect.translator import StorageConfig

        return StorageConfig(
            storage_type="s3",
            endpoint="http://minio:9000",
            bucket="nova-stages",
            base_prefix="db/sch/stage1",
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
        )

    async def _run(self, monkeypatch, error=None):
        import app.modules.query.service as service_module

        sink = AuditSink()
        repo = RecordingRepo(error=error)
        svc = QueryService()
        svc._repo = repo

        async def fake_configs(parsed, **kwargs):
            return parsed, {ref.start: self._storage_config() for ref in parsed.stage_refs}

        async def fake_csv(parsed, stage_configs):
            return {}, None

        svc._resolve_stage_refs = fake_configs
        svc._detect_csv_params = fake_csv

        monkeypatch.setattr(service_module, "write_audit_log", sink)
        monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")
        # Injection moved into the shared pipeline (NOVA-28 PR 3).
        import app.modules.query.sql_pipeline as pipeline_module

        monkeypatch.setattr(
            pipeline_module,
            "get_credential_params",
            lambda *args, **kwargs: {
                "aws.s3.access_key": ACCESS_KEY,
                "aws.s3.secret_key": SECRET_KEY,
            },
        )
        result = await svc.execute(
            sql="SELECT * FROM @stage1.data.csv",
            username="analyst",
            encrypted_password="enc",
        )
        return result, repo, sink

    async def test_nothing_credential_bearing_leaves_the_process(self, monkeypatch):
        result, repo, sink = await self._run(monkeypatch)

        # Engine keeps the real credentials — @stage must still work.
        assert f"'{ACCESS_KEY}'" in repo.calls[-1]
        assert f"'{SECRET_KEY}'" in repo.calls[-1]

        # Nothing persisted, nothing returned.
        for secret in (ACCESS_KEY, SECRET_KEY):
            assert secret not in result.executed_sql
            assert secret not in result.original_sql
            for stored in sink.persisted_sql:
                assert secret not in stored

    async def test_error_path_on_stage_query_also_redacted(self, monkeypatch):
        with pytest.raises(RuntimeError):
            await self._run(monkeypatch, error=RuntimeError("stage read failed"))

    async def test_original_sql_is_the_user_stage_reference(self, monkeypatch):
        """``original_sql`` stays user text — it is not the rewritten form."""
        result, _repo, _sink = await self._run(monkeypatch)
        assert result.original_sql == "SELECT * FROM @stage1.data.csv"
        assert "FILES(" not in result.original_sql


class TestRedactSqlCredentialsUnit:
    """The helper itself — providers, shapes, and no over-redaction."""

    def test_redacts_aws_credentials(self, redact):
        out = redact(_credential_sql())
        assert out == (
            "SELECT * FROM FILES('path'='s3://stages/x.csv', 'format'='csv', "
            "'aws.s3.access_key'='***', "
            "'aws.s3.secret_key'='***', "
            "'aws.s3.session_token'='***')"
        )

    @pytest.mark.parametrize(
        "param",
        [
            "azure.account_key",
            "azure.sas_token",
            "gcs.service_account_key",
            "aws.s3.access_key",
            "aws.s3.secret_key",
            "aws.s3.session_token",
        ],
    )
    def test_redacts_every_secret_bearing_param(self, redact, param):
        sql = f"SELECT * FROM FILES('{param}'='TESTVALUE_redactable')"
        out = redact(sql)
        assert "TESTVALUE_redactable" not in out
        assert f"'{param}'='***'" in out

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM FILES('path'='s3://b/k', 'format'='csv')",
            "SELECT 'aws.s3.secret_key', 'not a value'",
            "SELECT * FROM t WHERE c = 'aws.s3.access_key'",
            "SELECT 1",
            "",
        ],
    )
    def test_non_credential_sql_is_unchanged(self, redact, sql):
        assert redact(sql) == sql

    def test_non_secret_s3_params_are_preserved(self, redact):
        """``endpoint``/``enable_ssl`` are config, not credentials."""
        sql = (
            "SELECT * FROM FILES('path'='s3://b/k', "
            "'aws.s3.endpoint'='http://minio:9000', "
            "'aws.s3.enable_ssl'='false')"
        )
        assert redact(sql) == sql

    def test_path_and_format_are_preserved(self, redact):
        sql = "SELECT * FROM FILES('path'='s3://bucket/a/b.csv', 'format'='csv')"
        assert redact(sql) == sql

    def test_idempotent(self, redact):
        once = redact(_credential_sql())
        assert redact(once) == once

    def test_bare_key_assignment_redacted(self, redact):
        out = redact(
            "SELECT * FROM FILES('path'='s3://b/k', aws.s3.secret_key='BARE_TESTVALUE')"
        )
        assert "BARE_TESTVALUE" not in out
        assert "aws.s3.secret_key='***'" in out

    def test_backticked_key_assignment_redacted(self, redact):
        out = redact("SELECT * FROM FILES(`aws.s3.secret_key`='BT_TESTVAL')")
        assert "BT_TESTVAL" not in out

    def test_double_quoted_key_assignment_redacted(self, redact):
        out = redact('SELECT * FROM FILES("aws.s3.secret_key" => \'DQ_TESTVAL\')')
        assert "DQ_TESTVAL" not in out

    def test_multiple_files_calls_all_redacted(self, redact):
        one = _credential_sql()
        sql = f"SELECT * FROM ({one}) a JOIN ({one}) b ON a.v = b.v"
        out = redact(sql)
        for secret in CREDENTIAL_PARAMS.values():
            assert secret not in out

    def test_case_insensitive_param_matching(self, redact):
        out = redact("SELECT * FROM FILES('AWS.S3.SECRET_KEY'='CI_TESTVAL')")
        assert "CI_TESTVAL" not in out


class TestValueQuoteMatrix:
    """NOVA-10: every quote style on the *value* must be redacted.

    The original patterns pinned the value to ``'[^']*'``. The key already
    accepted single/double/backtick/bare spellings, but a value written with
    double quotes — ``'aws.s3.access_key'="AKIA…"`` — was left untouched and the
    raw credential reached ``NOVA_SYSTEM.AUDIT_LOG`` and the API response.

    The table below is the issue's acceptance matrix plus the combinations the
    first fix missed: a bare key with a double-quoted value
    (``aws.s3.secret_key="…"``) had no pattern at all and only survived because
    the verification pass raised. Redaction now covers it directly.
    """

    PLACEHOLDER = "AKIA_MATRIX_PLACEHOLDER"

    @pytest.mark.parametrize(
        "key,value",
        [
            pytest.param("'aws.s3.access_key'", "'AKIA_MATRIX_PLACEHOLDER'", id="s/s"),
            pytest.param("'aws.s3.access_key'", '"AKIA_MATRIX_PLACEHOLDER"', id="s/d"),
            pytest.param('"aws.s3.access_key"', '"AKIA_MATRIX_PLACEHOLDER"', id="d/d"),
            pytest.param("'aws.s3.access_key'", '" AKIA_MATRIX_PLACEHOLDER "', id="s/spaced/d"),
            pytest.param("aws.s3.access_key", '"AKIA_MATRIX_PLACEHOLDER"', id="bare/d"),
            pytest.param("`aws.s3.access_key`", '"AKIA_MATRIX_PLACEHOLDER"', id="backtick/d"),
            pytest.param('"aws.s3.access_key"', "'AKIA_MATRIX_PLACEHOLDER'", id="d/s"),
        ],
    )
    def test_key_and_value_quote_combinations(self, redact, key, value):
        sql = f"SELECT * FROM FILES('path'='s3://b/k', {key} = {value})"
        out = redact(sql)
        assert self.PLACEHOLDER not in out, f"credential survived in {out!r}"

    @pytest.mark.parametrize(
        "key,value",
        [
            pytest.param("'aws.s3.secret_key'", "'AKIA_MATRIX_PLACEHOLDER'", id="single=>"),
            pytest.param("'aws.s3.secret_key'", '"AKIA_MATRIX_PLACEHOLDER"', id="double=>"),
            pytest.param('"aws.s3.secret_key"', '"AKIA_MATRIX_PLACEHOLDER"', id="dq-key=>"),
            pytest.param("aws.s3.secret_key", '"AKIA_MATRIX_PLACEHOLDER"', id="bare=>"),
        ],
    )
    def test_arrow_operator_with_every_quote_style(self, redact, key, value):
        sql = f"SELECT * FROM FILES({key} => {value})"
        out = redact(sql)
        assert self.PLACEHOLDER not in out, f"credential survived in {out!r}"

    def test_escaped_quote_inside_value_is_redacted(self, redact):
        """``''`` escapes a quote inside a literal; the literal is one value."""
        out = redact("FILES('aws.s3.secret_key'='it''s a SECRET_ESCAPED')")
        assert "SECRET_ESCAPED" not in out
        assert "'aws.s3.secret_key'='***'" in out

    def test_non_credential_params_are_not_rewritten(self, redact):
        sql = (
            "SELECT * FROM FILES('path'='s3://b/k', 'format'='csv', "
            "'aws.s3.endpoint'=\"http://minio:9000\")"
        )
        assert redact(sql) == sql

    def test_double_quoted_non_credential_value_preserved(self, redact):
        """A double-quoted value on a *config* param is not a secret to mask."""
        sql = "SELECT * FROM FILES('aws.s3.enable_ssl'=\"false\")"
        assert redact(sql) == sql

    def test_idempotent_for_the_double_quoted_value_form(self, redact):
        once = redact("FILES('aws.s3.access_key'=\"PLACEHOLDER_V\")")
        assert redact(once) == once
        assert "'aws.s3.access_key'='***'" in once


class TestDottedCredentialSpelling:
    """A credential suffix written with dots, not underscores, is the same param.

    ``CREDENTIAL_PARAM_SUFFIXES`` matches on the final segment, so
    ``azure.account_key`` matched but ``azure.account.key`` ended in ``key`` and
    matched nothing — neither the redaction patterns nor the verification pass.
    StarRocks accepts the dotted spelling, so a user wrote it straight into
    ``FILES()`` and the value reached the audit row and the API response.
    """

    PLACEHOLDER = "SECRETBASE64_PLACEHOLDER=="

    @pytest.mark.parametrize(
        "param",
        [
            "azure.account.key",
            "azure.blob.account.key",
            "gcs.service.account.key",
            "aws.s3.access.key",
        ],
    )
    def test_dotted_spelling_is_redacted(self, redact, param):
        sql = f"SELECT * FROM FILES('{param}'='{self.PLACEHOLDER}')"
        out = redact(sql)
        assert self.PLACEHOLDER not in out, f"dotted credential survived: {out!r}"

    @pytest.mark.parametrize(
        "param",
        [
            "azure.account_key",
            "azure.account.key",
            "gcs.service_account_key",
            "gcs.service.account.key",
        ],
    )
    def test_underscore_and_dotted_spellings_both_covered(self, redact, param):
        """Neither spelling may fall out of coverage as suffixes are added."""
        sql = f"FILES('{param}'=\"{self.PLACEHOLDER}\")"
        assert self.PLACEHOLDER not in redact(sql)

    def test_dotted_spelling_is_caught_by_the_verification_pass(self):
        """The fail-closed scan must see the dotted form too, not just redaction."""
        from app.common.sql_guard import _POPULATED_CREDENTIAL, _normalized_value

        match = _POPULATED_CREDENTIAL.search(
            f"FILES(azure.account.key=\"{self.PLACEHOLDER}\")"
        )
        assert match is not None, "verification pattern missed the dotted spelling"
        assert _normalized_value(match.group("value")) == self.PLACEHOLDER

    def test_dotted_non_credential_suffix_untouched(self, redact):
        sql = "FILES('aws.s3.access.point'='not-a-secret')"
        assert redact(sql) == sql


class TestSanitizingResponseFailsClosedNotOpen:
    """The response boundary must never turn an unredactable string into a 500.

    ``SanitizingJSONResponse.render`` runs after the controller has committed to
    a status code, so an exception there replaces *every* response — including
    the error handler's own — with a 500. Failing closed means dropping the
    string, not crashing the response that carried it.
    """

    def test_unredactable_string_is_replaced_not_raised(self):
        from app.modules.query.router import SanitizingJSONResponse

        payload = {"note": "FILES('aws.s3.secret_key'='unterminated"}
        body = SanitizingJSONResponse(payload).render(payload).decode()

        assert SanitizingJSONResponse.REDACTION_FAILED_PLACEHOLDER in body
        assert "unterminated" not in body

    def test_redactable_payload_still_redacted(self):
        from app.modules.query.router import SanitizingJSONResponse

        payload = {"sql": "FILES('aws.s3.secret_key'=\"LEAKME\")"}
        body = SanitizingJSONResponse(payload).render(payload).decode()

        assert "LEAKME" not in body
        assert "***" in body
