"""Contract tests for ``success`` on ``POST /api/v1/query/explain``.

``test_query_success_contract.py`` pinned the contract on ``/query/execute``
(NOVA-14): ``success`` reflects ``QueryResult.error``, never the shape of the
result. The ``explain`` path has the identical failure site — ``@stage``
translation refuses a stage that does not exist — but it omitted ``error=``, so
``QueryResult.success`` stayed ``True`` for a statement that never reached the
engine and the router reported a false success.

These tests are the mirror image, for the same reason: only a request through
the real router and the real ``QueryService`` can catch a missing ``error=`` at
the one failure site ``explain`` has. Both directions are covered, because a
contract that only pins ``success=False`` on failure would pass just as well if
the fix over-blocked and never reported success at all.

``QueryResult`` objects are produced by the service's own paths, never
hand-assembled, so deleting ``error=`` from ``explain`` fails these tests.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import StarRocksError, register_exception_handlers
from app.modules.query.repository import QueryResult

EXPLAIN_ENDPOINT = "/api/v1/query/explain"

SELECT_SQL = "SELECT 1 AS x"
STAGE_SQL = "SELECT * FROM @stage1.data.csv"
MISSING_STAGE_SQL = "SELECT * FROM @nope.data.csv"

ACCESS_KEY = "AKIA_EXPLAIN_CONTRACT_TEST"
SECRET_KEY = "SECRET_EXPLAIN_CONTRACT_TEST"


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


class FakeEngine:
    """Stub repository standing in for StarRocks.

    ``explain`` prefixes the translated statement with ``EXPLAIN`` and returns
    the plan the engine produces. A statement the engine would reject raises
    ``StarRocksError`` exactly as ``repository.execute_as_user`` does.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.fail_with: StarRocksError | None = None

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        if self.fail_with is not None:
            raise self.fail_with
        return QueryResult(
            columns=["PLAN"],
            rows=[["EXPLAIN PLAN"]],
            row_count=1,
            executed_sql=sql,
        )


@pytest.fixture
def explain_client(monkeypatch):
    """The production router + service, with the engine and stage metadata stubbed."""
    import app.modules.query.service as service_module
    from app.core import deps as deps_module
    from app.modules.query.service import query_service

    engine = FakeEngine()

    async def fake_configs(database, schema):
        return {"stage1": _stage_config()}

    async def fake_csv_params(parsed, stage_configs):
        return {}, None

    monkeypatch.setattr(query_service, "_repo", engine)
    monkeypatch.setattr(query_service, "_load_stage_configs", fake_configs)
    monkeypatch.setattr(query_service, "_detect_csv_params", fake_csv_params)
    monkeypatch.setattr(service_module, "decrypt_password", lambda value: "pw")

    async def fake_current_user():
        return {
            "username": "analyst",
            "session_id": "sess-explain-success-contract",
            "roles": ["analyst"],  # deliberately non-admin
            "active_role": "analyst",
            "encrypted_password": "enc",
        }

    app = FastAPI()
    register_exception_handlers(app)
    from app.modules.query.router import router as query_router

    app.include_router(query_router, prefix="/api/v1/query")
    app.dependency_overrides[deps_module.get_current_user] = fake_current_user

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, engine


class TestFailedTranslationIsReportedAsFailure:
    """AC#1 — a stage that cannot be translated is never ``success: true``."""

    def test_unknown_stage_reports_success_false(self, explain_client):
        client, engine = explain_client
        engine.calls.clear()

        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": MISSING_STAGE_SQL}).json()

        assert payload["success"] is False, "a refused translation was reported as success"
        assert payload["error"], "the failure must be explicit on the result"
        assert "nope" in payload["error"]
        assert engine.calls == [], "a statement that cannot be translated must not be executed"

    def test_error_and_warning_agree_on_the_same_message(self, explain_client):
        """AC#3 — the ``❌ ...`` warning survives; ``error`` states the same reason."""
        client, _engine = explain_client

        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": MISSING_STAGE_SQL}).json()

        assert payload["warnings"], "the ❌ warning must be preserved"
        assert any(w.startswith("❌") for w in payload["warnings"])
        assert payload["error"] in payload["warnings"][0]


class TestSuccessfulExplainStaysSuccessful:
    """AC#2 — the fix must not over-block a plan that was produced."""

    def test_plain_select_reports_success_true(self, explain_client):
        client, engine = explain_client

        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": SELECT_SQL}).json()

        assert payload["success"] is True
        assert payload["error"] is None
        assert engine.calls == [f"EXPLAIN {SELECT_SQL}"]

    def test_at_stage_explain_reports_success_true(self, explain_client):
        """A translated ``@stage`` plan succeeds — the translation did not fail."""
        client, engine = explain_client

        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()

        assert payload["success"] is True, "a successful @stage explain read as failure"
        assert payload["error"] is None
        assert engine.calls and engine.calls[0].startswith("EXPLAIN SELECT * FROM FILES(")

    def test_warning_alone_never_means_failure(self, explain_client):
        """The class of bug: a non-empty ``warnings`` must not decide ``success``.

        The warning that reaches the response is the failure one (the successful
        translation's notice is dropped by ``explain``, which keeps
        ``executed_sql`` only). It therefore accompanies ``error``, and the plan
        below succeeds with a warning-bearing ``@stage`` translation that the
        response simply does not surface as a warning.
        """
        client, _engine = explain_client

        payload = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()

        assert payload["success"] is True
        assert payload["warnings"] == [], "explain surfaces no success warning"
        assert payload["error"] is None


class TestFieldSemantics:
    """``success`` stays a boolean and ``error`` states the failure directly."""

    def test_success_is_always_a_bool(self, explain_client):
        client, _engine = explain_client

        ok = client.post(EXPLAIN_ENDPOINT, json={"sql": SELECT_SQL}).json()
        failed = client.post(EXPLAIN_ENDPOINT, json={"sql": MISSING_STAGE_SQL}).json()

        assert ok["success"] is True
        assert failed["success"] is False
        assert isinstance(ok["success"], bool) and isinstance(failed["success"], bool)

    def test_response_keeps_the_fields_the_frontend_reads(self, explain_client):
        client, _engine = explain_client

        result = client.post(EXPLAIN_ENDPOINT, json={"sql": MISSING_STAGE_SQL}).json()

        for field in (
            "success",
            "columns",
            "rows",
            "row_count",
            "affected_rows",
            "elapsed_ms",
            "original_sql",
            "executed_sql",
            "warnings",
            "error",
        ):
            assert field in result, f"{field} disappeared from the response"


class TestExecuteContractIsUntouched:
    """AC#4 — NOVA-14's ``/query/execute`` behaviour is not changed by this fix.

    The mutation that matters: deleting ``error=`` from ``explain`` fails this
    module, while deleting ``error=`` from ``execute`` fails
    ``test_query_success_contract.py``. Neither fix can silently cover the other.
    """

    def test_translation_failure_site_is_shared_by_both_paths(self, explain_client):
        """Both paths mark the same ValueError the same way — no drift."""
        client, engine = explain_client
        engine.calls.clear()

        from app.modules.query.service import query_service

        async def no_stages(database, schema):
            return {}

        async def fake_csv_params(parsed, stage_configs):
            return {}, None

        original_configs = query_service._load_stage_configs
        original_csv = query_service._detect_csv_params
        query_service._load_stage_configs = no_stages
        query_service._detect_csv_params = fake_csv_params
        try:
            explain = client.post(EXPLAIN_ENDPOINT, json={"sql": STAGE_SQL}).json()
            execute = client.post("/api/v1/query/execute", json={"sql": STAGE_SQL}).json()[0]
        finally:
            query_service._load_stage_configs = original_configs
            query_service._detect_csv_params = original_csv

        assert explain["success"] is False
        assert execute["success"] is False
        assert explain["error"] == execute["error"], "the two paths disagree on the reason"
        assert explain["error"]
        assert engine.calls == [], "a statement that cannot be translated must not be executed"
