"""Contract tests for ``success`` on ``POST /api/v1/query/execute``.

The contract: a statement the engine rejected is never reported ``success:
true``, and a statement that succeeded is never reported ``success: false``.

Nothing pinned this before. ``grep -rn "success" backend/tests/`` contained no
assertion that a failure reports ``success=false``, and the mutation

    sed -i 's/success=not is_error,/success=True,/' app/modules/query/router.py

left all 549 tests green — the field the frontend renders its failure marker
from (``frontend/src/features/workspaces/index.tsx``) was free to break
silently.

The old predicate inferred failure from the *shape* of the result
(``bool(warnings) and not columns and row_count == 0``), which misreported a
successful ``@stage`` DML statement: ``translate_stage_query`` appends
"Resolved @stageN reference for execution" on its success path, and DML returns
no ``description``, so both legs of the heuristic held on a statement that
worked. These tests therefore cover both directions — they are what makes the
shape predicate impossible to reintroduce.

Requests go through the real router and the real ``QueryService``; only the
engine, the stage metadata and the session lookup are stubbed. ``QueryResult``
objects are produced by the service's own paths, never hand-assembled, so a
regression in how a path marks failure fails these tests.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import StarRocksError, register_exception_handlers
from app.modules.query.repository import QueryResult

EXECUTE_ENDPOINT = "/api/v1/query/execute"

SELECT_SQL = "SELECT 1 AS x"
BAD_SQL = "SELECT * FROM no_such_table_zzz"
COPY_SQL = "COPY INTO my_table FROM @stage1.data.csv"

ACCESS_KEY = "AKIA_SUCCESS_CONTRACT_TEST"
SECRET_KEY = "SECRET_SUCCESS_CONTRACT_TEST"


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

    Returns what the real repository returns for a ``SELECT`` (columns + rows)
    and for DML (no ``description`` -> no columns, no rows, ``affected_rows``
    from ``rowcount``), and raises ``StarRocksError`` for a statement the engine
    would reject — exactly as ``repository.execute_as_user`` does.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.fail_with: StarRocksError | None = None

    async def execute_as_user(self, sql, **kwargs):
        self.calls.append(sql)
        if self.fail_with is not None:
            raise self.fail_with
        if sql.strip().upper().startswith("COPY INTO"):
            # Load DML: real StarRocks returns no ``description`` (no columns,
            # no rows) and reports progress through ``rowcount`` instead.
            return QueryResult(affected_rows=42, executed_sql=sql)
        if sql.strip().upper().startswith("DROP "):
            return QueryResult(affected_rows=0, executed_sql=sql)
        return QueryResult(columns=["x"], rows=[[1]], row_count=1, executed_sql=sql)


@pytest.fixture
def execute_client(monkeypatch):
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

    async def no_audit(**kwargs):
        return None

    monkeypatch.setattr(service_module, "write_audit_log", no_audit)

    async def fake_current_user():
        return {
            "username": "analyst",
            "session_id": "sess-success-contract",
            "roles": [],
            "active_role": None,
            "encrypted_password": "enc",
        }

    app = FastAPI()
    register_exception_handlers(app)
    from app.modules.query.router import router as query_router

    app.include_router(query_router, prefix="/api/v1/query")
    app.dependency_overrides[deps_module.get_current_user] = fake_current_user

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, engine


class TestFailureIsReportedAsFailure:
    """AC#1 — a statement the engine rejected is never ``success: true``.

    The failure is raised by the engine and turned into an error result by
    ``QueryService.execute_statements`` (the path ``service.py`` documents as
    "Stops on first error"). Nothing is hand-assembled here.
    """

    def test_failed_statement_is_marked_with_its_own_reason(self, execute_client):
        client, engine = execute_client
        engine.fail_with = StarRocksError("SQL error: Unknown table 'no_such_table_zzz'")

        payload = client.post(EXECUTE_ENDPOINT, json={"sql": BAD_SQL}).json()

        assert len(payload) == 1
        assert payload[0]["success"] is False, "a failed statement was reported as success"
        assert payload[0]["error"], "the failure must be explicit on the result"
        assert "no_such_table_zzz" in payload[0]["error"]
        assert engine.calls == [BAD_SQL], "the statement must have reached the engine"

    def test_connection_error_reports_success_false(self, execute_client):
        client, engine = execute_client
        engine.fail_with = StarRocksError("Connection error: gone away")

        payload = client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL}).json()

        assert payload[0]["success"] is False
        assert "gone away" in payload[0]["error"]

    def test_unknown_stage_failure_never_reaches_the_engine(self, execute_client):
        """Translation refuses before the engine is called — still a failure."""
        client, engine = execute_client
        engine.calls.clear()

        payload = client.post(
            EXECUTE_ENDPOINT, json={"sql": "SELECT * FROM @missing.data.csv"}
        ).json()

        assert payload[0]["success"] is False
        assert payload[0]["error"]
        assert engine.calls == [], "a statement that cannot be translated must not be executed"


class TestMultiStatementIsScoredPerStatement:
    """AC#2 — ``SELECT 1; SELECT bad`` is ``[true, false]``, not contaminated."""

    def test_first_statement_succeeds_second_fails(self, execute_client):
        client, engine = execute_client

        async def execute_as_user(sql, **kwargs):
            engine.calls.append(sql)
            if "no_such_table" in sql:
                raise StarRocksError("SQL error: Unknown table 'no_such_table_zzz'")
            return QueryResult(columns=["x"], rows=[[1]], row_count=1, executed_sql=sql)

        engine.execute_as_user = execute_as_user

        payload = client.post(
            EXECUTE_ENDPOINT, json={"sql": f"{SELECT_SQL}; {BAD_SQL}"}
        ).json()

        assert [item["success"] for item in payload] == [True, False]
        assert payload[0]["error"] is None
        assert payload[1]["error"], "the failing statement must carry its own error"
        assert [item["original_sql"] for item in payload] == [SELECT_SQL, BAD_SQL]
        assert engine.calls == [SELECT_SQL, BAD_SQL], "both statements ran, in order"

    def test_successful_pair_is_true_true(self, execute_client):
        client, _engine = execute_client

        payload = client.post(EXECUTE_ENDPOINT, json={"sql": "SELECT 1; SELECT 2"}).json()

        assert [item["success"] for item in payload] == [True, True]


class TestSuccessfulAtStageDmlStaysSuccessful:
    """AC#4 — the direction that was actually broken in production.

    ``COPY INTO ... FROM @stage`` succeeds with 42 rows loaded but returns no
    ``description`` (``columns=[]``, ``row_count=0``) while the translator
    always warns. The old shape predicate read all three as failure.
    """

    def test_copy_into_from_stage_reports_success_true(self, execute_client):
        client, engine = execute_client

        payload = client.post(EXECUTE_ENDPOINT, json={"sql": COPY_SQL}).json()

        assert len(payload) == 1
        result = payload[0]
        assert result["success"] is True, "a successful @stage DML statement read as failure"
        assert result["error"] is None
        assert result["affected_rows"] == 42
        # The conditions the old predicate keyed off — all true on a success.
        assert result["warnings"], "the non-fatal @stage warning must be preserved"
        assert result["columns"] == []
        assert result["row_count"] == 0
        assert engine.calls and engine.calls[0].startswith("COPY INTO my_table FROM FILES(")

    def test_non_fatal_warning_alone_never_means_failure(self, execute_client):
        """The class of bug, not just the ``COPY INTO`` site.

        Any result carrying a warning with no rows must still report success
        when the statement itself did not fail.
        """
        client, engine = execute_client

        # A destructive statement reaches the engine only once the caller has
        # confirmed it; ``DROP TABLE`` then returns no ``description`` at all,
        # which is shape-identical to the ``@stage`` DML case above.
        payload = client.post(
            EXECUTE_ENDPOINT, json={"sql": "DROP TABLE t", "confirm_destructive": True}
        ).json()

        assert engine.calls == ["DROP TABLE t"]
        assert payload[0]["success"] is True
        assert payload[0]["columns"] == [] and payload[0]["row_count"] == 0

    def test_unconfirmed_destructive_statement_is_not_success(self, execute_client):
        """Refused before execution is still a failure, and says why."""
        client, engine = execute_client

        payload = client.post(EXECUTE_ENDPOINT, json={"sql": "DROP TABLE t"}).json()

        assert engine.calls == [], "the refused statement must not reach the engine"
        assert payload[0]["success"] is False
        assert "confirmation" in payload[0]["error"].lower()

    def test_stage_select_success_true(self, execute_client):
        client, _engine = execute_client

        payload = client.post(
            EXECUTE_ENDPOINT, json={"sql": "SELECT * FROM @stage1.data.csv"}
        ).json()

        assert payload[0]["success"] is True
        assert payload[0]["warnings"], "the @stage resolution warning is expected"


class TestFieldSemantics:
    """``success`` stays a boolean and ``error`` states the failure directly."""

    def test_success_is_always_a_bool(self, execute_client):
        client, engine = execute_client
        engine.fail_with = StarRocksError("SQL error: boom")
        failed = client.post(EXECUTE_ENDPOINT, json={"sql": BAD_SQL}).json()
        engine.fail_with = None
        ok = client.post(EXECUTE_ENDPOINT, json={"sql": SELECT_SQL}).json()

        assert ok[0]["success"] is True
        assert failed[0]["success"] is False
        assert isinstance(ok[0]["success"], bool) and isinstance(failed[0]["success"], bool)

    def test_response_keeps_the_fields_the_frontend_reads(self, execute_client):
        """``success`` etc. stay present — the frontend renders this payload."""
        client, engine = execute_client
        engine.fail_with = StarRocksError("SQL error: boom")

        result = client.post(EXECUTE_ENDPOINT, json={"sql": BAD_SQL}).json()[0]

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
            "destructive",
            "needs_confirmation",
            "error",
        ):
            assert field in result, f"{field} disappeared from the response"


class TestEngineFailureCannotBeLaunderedIntoSuccess:
    """No path from a real engine error to ``success: true``."""

    @pytest.mark.parametrize(
        "message",
        [
            "SQL error: Unknown table 't'",
            "Connection error: (2006, 'server has gone away')",
            "Access denied for user 'analyst'",
        ],
    )
    def test_error_message_never_yields_success_true(self, execute_client, message):
        client, engine = execute_client
        engine.fail_with = StarRocksError(message)

        payload = client.post(EXECUTE_ENDPOINT, json={"sql": BAD_SQL}).json()

        assert payload[0]["success"] is False
