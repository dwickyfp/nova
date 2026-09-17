"""Unit tests for the proxy's query bridge.

``QueryService`` is replaced with a fake, so these tests pin the *contract*
between the proxy and the pipeline it reuses rather than re-testing the
pipeline itself:

* every engine statement goes through ``execute_statements`` (the single path
  that runs the guard, the ``@stage`` translation and the audit write);
* ``SET`` and ``USE`` never appear in what reaches the engine;
* a ``QueryResult``'s columns/rows/affected_rows map onto the right wire shape;
* ``error`` becomes an ERROR packet, and no credential value survives into the
  response.

A fake is used rather than ``unittest.mock`` because the assertions are about
what the proxy *did* to a sequence of calls, which is exactly what a small
recording object expresses more clearly than patch stacks.
"""

from __future__ import annotations

import pytest

from app.modules.query.repository import QueryResult
from app.proxy.executor import ProxyQueryExecutor, WireResult
from app.proxy.session import SessionState


class FakeQueryService:
    """Records calls and returns queued results."""

    def __init__(self, results: list[QueryResult] | None = None) -> None:
        self.calls: list[dict] = []
        self._results = results or [QueryResult()]

    async def execute_statements(self, **kwargs) -> list[QueryResult]:
        self.calls.append(kwargs)
        return self._results

    @property
    def last_sql(self) -> str:
        return self.calls[-1]["sql"]

    @property
    def last_database(self):
        return self.calls[-1]["database"]

    @property
    def last_role(self):
        return self.calls[-1]["role"]


@pytest.fixture
def fake_service(monkeypatch):
    fake = FakeQueryService()
    monkeypatch.setattr("app.proxy.executor.query_service", fake)
    return fake


def _patch_service(monkeypatch, fake: FakeQueryService) -> FakeQueryService:
    monkeypatch.setattr("app.proxy.executor.query_service", fake)
    return fake


class TestRoutingToThePipeline:
    async def test_select_goes_through_execute_statements(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute(
            "SELECT 1", username="u", connection=object()
        )
        assert isinstance(result, WireResult)
        assert len(fake_service.calls) == 1
        assert fake_service.last_sql == "SELECT 1"

    async def test_session_database_is_passed_to_the_pipeline(self, fake_service):
        session = SessionState(database="NOVA_DEMO")
        executor = ProxyQueryExecutor(session)
        await executor.execute("SELECT 1", username="u", connection=object())
        assert fake_service.last_database == "NOVA_DEMO"

    async def test_use_updates_context_and_never_reaches_the_engine(self, fake_service):
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        result = await executor.execute("USE NOVA_DEMO", username="u", connection=object())

        assert result.is_ok
        assert session.database == "NOVA_DEMO"
        assert fake_service.calls == []

    async def test_set_is_consumed_and_never_reaches_the_engine(self, fake_service):
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        result = await executor.execute("SET @x = 1", username="u", connection=object())

        assert result.is_ok
        assert session.user_variables["x"] == "1"
        assert fake_service.calls == []

    async def test_a_script_of_set_statements_only_yields_ok(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute(
            "SET @a = 1; SET @b = 2", username="u", connection=object()
        )
        assert result.is_ok
        assert result.ok_affected == 0
        assert fake_service.calls == []

    async def test_set_is_stripped_from_a_mixed_script(self, fake_service):
        """``SET @x = 1; SELECT 2`` must reach the engine as just ``SELECT 2``.

        Forwarding the ``SET`` would fail with ``Stage 'x' not found`` — the
        reason the proxy owns ``SET`` at all.
        """
        executor = ProxyQueryExecutor(SessionState())
        await executor.execute("SET @x = 1; SELECT 2", username="u", connection=object())

        assert len(fake_service.calls) == 1
        assert "SET" not in fake_service.last_sql.upper()
        assert fake_service.last_sql == "SELECT 2"

    async def test_use_mid_script_takes_effect(self, fake_service):
        session = SessionState()
        executor = ProxyQueryExecutor(session)
        await executor.execute("USE NOVA_DEMO", username="u", connection=object())
        await executor.execute("SELECT 1", username="u", connection=object())
        assert fake_service.last_database == "NOVA_DEMO"

    async def test_set_role_reaches_the_pipeline_as_the_role(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        await executor.execute("SET ROLE ACCOUNTADMIN", username="u", connection=object())
        await executor.execute("SELECT 1", username="u", connection=object())
        assert fake_service.last_role == "ACCOUNTADMIN"

    async def test_empty_query_is_an_error(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute("   ", username="u", connection=object())
        assert result.error is not None
        assert fake_service.calls == []

    async def test_set_global_is_reported_as_an_error(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute(
            "SET @@global.x = 1", username="u", connection=object()
        )
        assert result.error is not None
        assert fake_service.calls == []


class TestResultMapping:
    async def test_columns_and_rows_become_a_resultset(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(
                    columns=["id", "name"],
                    rows=[[1, "Alice"], [2, "Bob"]],
                    row_count=2,
                    executed_sql="SELECT id, name FROM people",
                )
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.is_resultset
        assert [c.name for c in result.columns] == ["id", "name"]
        assert result.rows == [[1, "Alice"], [2, "Bob"]]

    async def test_dml_without_columns_is_an_ok_packet(self, monkeypatch):
        fake = FakeQueryService(
            [QueryResult(affected_rows=3, executed_sql="DELETE FROM t")]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("DELETE FROM t", username="u", connection=object())

        assert result.is_ok
        assert result.ok_affected == 3

    async def test_error_result_becomes_an_error_response(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(
                    original_sql="SELECT * FROM nope",
                    executed_sql="SELECT * FROM nope",
                    warnings=["unknown table"],
                    error="unknown table 'nope'",
                )
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.error == "unknown table 'nope'"
        assert result.error_code == 1064
        assert not result.is_resultset

    async def test_error_wins_over_an_earlier_resultset(self, monkeypatch):
        """A multi-statement script stops at the first error.

        The engine returned a result for statement one and an error for
        statement two; the client must see the error, not statement one's rows.
        """
        fake = FakeQueryService(
            [
                QueryResult(columns=["a"], rows=[[1]], row_count=1),
                QueryResult(original_sql="SELECT nope", error="bad sql"),
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1; SELECT nope", username="u", connection=object())

        assert result.error == "bad sql"
        assert not result.is_resultset

    async def test_last_resultset_wins_for_a_clean_script(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(affected_rows=1),
                QueryResult(columns=["b"], rows=[[2]], row_count=1),
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1; SELECT 2", username="u", connection=object())

        assert result.is_resultset
        assert [c.name for c in result.columns] == ["b"]

    async def test_affected_rows_accumulate_across_statements(self, monkeypatch):
        fake = FakeQueryService(
            [QueryResult(affected_rows=2), QueryResult(affected_rows=3)]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute(
            "DELETE FROM a; DELETE FROM b", username="u", connection=object()
        )

        assert result.ok_affected == 5

    async def test_no_results_is_an_ok(self, monkeypatch):
        fake = FakeQueryService([])
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.is_ok
        assert result.ok_affected == 0

    async def test_null_values_survive_the_mapping(self, monkeypatch):
        fake = FakeQueryService(
            [QueryResult(columns=["a", "b"], rows=[[1, None]], row_count=1)]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.rows == [[1, None]]

    def test_integer_columns_get_an_integer_type(self, monkeypatch):
        from app.proxy.protocol import TYPE_LONGLONG, TYPE_VAR_STRING

        executor = ProxyQueryExecutor(SessionState())
        wire = executor._to_wire(
            [QueryResult(columns=["n", "s"], rows=[[7, "x"]], row_count=1)]
        )
        assert wire.columns[0].type_code == TYPE_LONGLONG
        assert wire.columns[1].type_code == TYPE_VAR_STRING

    def test_column_type_falls_back_to_varchar_for_all_nulls(self):
        from app.proxy.protocol import TYPE_VAR_STRING

        executor = ProxyQueryExecutor(SessionState())
        wire = executor._to_wire(
            [QueryResult(columns=["a"], rows=[[None]], row_count=1)]
        )
        assert wire.columns[0].type_code == TYPE_VAR_STRING


class TestShowDatabasesFiltering:
    """``NOVA_SYSTEM`` must not be visible to any client."""

    async def test_nova_system_is_removed(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(
                    columns=["Database"],
                    rows=[["NOVA_DEMO"], ["NOVA_SYSTEM"], ["NOVA_ANALYTICS"]],
                    row_count=3,
                )
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SHOW DATABASES", username="u", connection=object())

        assert result.is_resultset
        assert [row[0] for row in result.rows] == ["NOVA_DEMO", "NOVA_ANALYTICS"]

    async def test_engine_internals_are_removed(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(
                    columns=["Database"],
                    rows=[
                        ["NOVA_DEMO"],
                        ["information_schema"],
                        ["sys"],
                        ["_statistics_"],
                    ],
                    row_count=4,
                )
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SHOW DATABASES", username="u", connection=object())

        assert [row[0] for row in result.rows] == ["NOVA_DEMO"]

    async def test_show_schemas_alias_is_filtered_too(self, monkeypatch):
        fake = FakeQueryService(
            [QueryResult(columns=["Database"], rows=[["NOVA_SYSTEM"], ["db1"]], row_count=2)]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SHOW SCHEMAS", username="u", connection=object())

        assert [row[0] for row in result.rows] == ["db1"]

    async def test_show_databases_error_passes_through(self, monkeypatch):
        fake = FakeQueryService([QueryResult(error="permission denied")])
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SHOW DATABASES", username="u", connection=object())

        assert result.error == "permission denied"


class TestCredentialRedaction:
    """Credentials must not survive into anything the proxy puts on the wire."""

    async def test_error_message_credentials_are_redacted(self, monkeypatch):
        leaking = (
            "Access storage error in FILES('path'='s3://b/f.csv', "
            "'aws.s3.access_key'='AKIAIOSFODNN7EXAMPLE', "
            "'aws.s3.secret_key'='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY')"
        )
        fake = FakeQueryService([QueryResult(original_sql="x", error=leaking)])
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.error is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in result.error
        assert "wJalrXUtnFEMI" not in result.error
        assert "***" in result.error

    async def test_warning_credentials_are_redacted(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(
                    columns=["a"],
                    rows=[[1]],
                    row_count=1,
                    warnings=[
                        "FILES('path'='s3://b/f.csv', 'aws.s3.secret_key'='wJalrXUtnFEMI/K7MDENG')"
                    ],
                )
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert all("wJalrXUtnFEMI" not in warning for warning in result.warnings)
        assert any("***" in warning for warning in result.warnings)

    async def test_unredactable_message_is_replaced_entirely(self, monkeypatch):
        """Fails closed: if redaction raises, the message is dropped.

        ``redact_sql_credentials`` refuses to return a statement it cannot fully
        redact. Shipping the raw message would be a leak, so the client gets a
        generic string instead.
        """

        class BoomQueryResult(QueryResult):
            def __init__(self) -> None:
                self.columns = []
                self.rows = []
                self.row_count = 0
                self.affected_rows = 0
                self.elapsed_ms = 0.0
                self.original_sql = ""
                self.executed_sql = ""
                self.warnings = []
                self.error = "boom"

        fake = FakeQueryService([BoomQueryResult()])
        _patch_service(monkeypatch, fake)

        def _raise(_: str) -> str:
            raise RuntimeError("cannot redact")

        monkeypatch.setattr("app.proxy.executor.redact_sql_credentials", _raise)

        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.error == "Query failed"


class TestFailureIsolation:
    async def test_pipeline_exception_becomes_an_error_not_a_crash(self, monkeypatch):
        class ExplodingService:
            async def execute_statements(self, **kwargs):
                raise RuntimeError("engine down")

        monkeypatch.setattr("app.proxy.executor.query_service", ExplodingService())
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.error is not None
        assert result.error_code == 1064
