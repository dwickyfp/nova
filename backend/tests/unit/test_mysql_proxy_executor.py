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

from app.modules.access_control.role_activation import RoleAssignments
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


class FakeRoleActivationService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def activate(
        self,
        connection,
        *,
        principal: str,
        requested_role: str,
        known_default_role: str | None = None,
    ):
        self.calls.append(
            {
                "connection": connection,
                "principal": principal,
                "requested_role": requested_role,
                "known_default_role": known_default_role,
            }
        )
        return requested_role, RoleAssignments(
            assigned_roles=("marketing", "ACCOUNTADMIN"),
            default_role="marketing",
        )


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
        result = await executor.execute("SELECT 1", username="u", connection=object())
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
        result = await executor.execute("SET @a = 1; SET @b = 2", username="u", connection=object())
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

    async def test_set_role_is_validated_before_it_reaches_the_pipeline_as_the_role(
        self, fake_service, monkeypatch
    ):
        activation = FakeRoleActivationService()
        monkeypatch.setattr("app.proxy.executor.role_activation_service", activation)
        session = SessionState(
            principal="u",
            assigned_roles=("marketing", "ACCOUNTADMIN"),
            default_role="marketing",
            active_role="marketing",
        )
        connection = object()
        executor = ProxyQueryExecutor(session)

        result = await executor.execute(
            "SET ROLE ACCOUNTADMIN", username="u", connection=connection
        )
        await executor.execute("SELECT 1", username="u", connection=object())

        assert result.is_ok
        assert activation.calls == [
            {
                "connection": connection,
                "principal": "u",
                "requested_role": "ACCOUNTADMIN",
                "known_default_role": "marketing",
            }
        ]
        assert fake_service.last_role == "ACCOUNTADMIN"
        assert session.security_context_version == 2

    async def test_empty_query_is_an_error(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute("   ", username="u", connection=object())
        assert result.error is not None
        assert fake_service.calls == []

    async def test_set_global_is_reported_as_an_error(self, fake_service):
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute("SET @@global.x = 1", username="u", connection=object())
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
        fake = FakeQueryService([QueryResult(affected_rows=3, executed_sql="DELETE FROM t")])
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
        fake = FakeQueryService([QueryResult(affected_rows=2), QueryResult(affected_rows=3)])
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
        fake = FakeQueryService([QueryResult(columns=["a", "b"], rows=[[1, None]], row_count=1)])
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.rows == [[1, None]]

    def test_integer_columns_get_an_integer_type(self, monkeypatch):
        from app.proxy.protocol import TYPE_LONGLONG, TYPE_VAR_STRING

        executor = ProxyQueryExecutor(SessionState())
        wire = executor._to_wire([QueryResult(columns=["n", "s"], rows=[[7, "x"]], row_count=1)])
        assert wire.columns[0].type_code == TYPE_LONGLONG
        assert wire.columns[1].type_code == TYPE_VAR_STRING

    def test_column_type_falls_back_to_varchar_for_all_nulls(self):
        from app.proxy.protocol import TYPE_VAR_STRING

        executor = ProxyQueryExecutor(SessionState())
        wire = executor._to_wire([QueryResult(columns=["a"], rows=[[None]], row_count=1)])
        assert wire.columns[0].type_code == TYPE_VAR_STRING


class TestColumnTypeInference:
    """NOVA-26 — the type code must reflect the engine's type, not a guess.

    Clients dispatch on this code to decide the Python/Java object they build,
    so a ``DECIMAL`` sent as ``TYPE_VAR_STRING`` arrives as ``'1.5'`` and breaks
    arithmetic. The row comparison in the old docstring — "a wrong guess costs
    nothing except a client's column-width estimate" — was simply false.
    """

    @staticmethod
    def _type_of(value) -> int:
        """Type code for a column whose sampled values are ``value``.

        ``_column_definition`` expects the column's values across sampled rows,
        so a bare value is wrapped and a list is taken as the samples.
        """
        from app.proxy.executor import _column_definition

        samples = value if isinstance(value, list) else [value]
        return _column_definition("c", samples).type_code

    def test_decimal_maps_to_newdecimal(self):
        from decimal import Decimal

        from app.proxy.protocol import TYPE_NEWDECIMAL

        assert self._type_of(Decimal("1.5")) == TYPE_NEWDECIMAL

    def test_date_maps_to_date(self):
        import datetime

        from app.proxy.protocol import TYPE_DATE

        assert self._type_of(datetime.date(2026, 1, 15)) == TYPE_DATE

    def test_datetime_maps_to_datetime_not_date(self):
        """``datetime`` subclasses ``date``; the narrower branch must win."""
        import datetime

        from app.proxy.protocol import TYPE_DATETIME

        assert self._type_of(datetime.datetime(2026, 1, 15, 10, 30)) == TYPE_DATETIME

    def test_timedelta_maps_to_time(self):
        import datetime

        from app.proxy.protocol import TYPE_TIME

        assert self._type_of(datetime.timedelta(hours=1, minutes=30)) == TYPE_TIME

    def test_time_maps_to_time(self):
        import datetime

        from app.proxy.protocol import TYPE_TIME

        assert self._type_of(datetime.time(10, 30)) == TYPE_TIME

    def test_float_maps_to_double(self):
        from app.proxy.protocol import TYPE_DOUBLE

        assert self._type_of(1.5) == TYPE_DOUBLE

    def test_int_maps_to_longlong(self):
        from app.proxy.protocol import TYPE_LONGLONG

        assert self._type_of(7) == TYPE_LONGLONG

    def test_str_maps_to_var_string(self):
        from app.proxy.protocol import TYPE_VAR_STRING

        assert self._type_of("x") == TYPE_VAR_STRING

    def test_leading_null_does_not_hide_the_real_type(self):
        """A null sample must be skipped, not treated as an unknown value.

        ``sample_values`` is one column's values across the sampled rows; a
        ``NULL`` in the first row is common and must not demote the column.
        """
        from decimal import Decimal

        from app.proxy.protocol import TYPE_NEWDECIMAL

        assert self._type_of([None, None, Decimal("1.5")]) == TYPE_NEWDECIMAL

    def test_all_null_column_has_no_type_to_infer(self):
        from app.proxy.protocol import TYPE_NULL, TYPE_VAR_STRING

        # A VARCHAR is the safe wire default; MySQL itself reports VARCHAR for
        # an all-NULL expression, so the client sees a string of NULLs.
        assert self._type_of([None, None]) == TYPE_VAR_STRING
        assert TYPE_NULL != TYPE_VAR_STRING

    def test_mixed_numeric_resultset_keeps_both_types_distinct(self):
        """The QA case: ``SELECT 1.5 AS a, 2/3 AS b`` in one row.

        Before the fix ``a`` was a string and ``b`` a float; the type must now
        follow the value the engine produced, not the position in the row.
        """
        from decimal import Decimal

        from app.proxy.protocol import TYPE_DOUBLE, TYPE_NEWDECIMAL

        executor = ProxyQueryExecutor(SessionState())
        wire = executor._to_wire(
            [
                QueryResult(
                    columns=["a", "b"],
                    rows=[[Decimal("1.5"), 0.6666666666666666]],
                    row_count=1,
                )
            ]
        )
        assert wire.columns[0].type_code == TYPE_NEWDECIMAL
        assert wire.columns[1].type_code == TYPE_DOUBLE

    def test_decimal_values_stay_numeric_text_with_the_right_code(self):
        """The text is unchanged; only the code that labels it is corrected."""
        from decimal import Decimal

        from app.proxy.protocol import build_text_row

        executor = ProxyQueryExecutor(SessionState())
        wire = executor._to_wire([QueryResult(columns=["d"], rows=[[Decimal("1.5")]], row_count=1)])
        assert build_text_row(wire.rows[0], wire.columns) == b"\x031.5"

    def test_bytes_map_to_blob_not_var_string(self):
        """A binary column must not be labelled as a character type."""
        from app.proxy.protocol import TYPE_BLOB

        assert self._type_of(b"\x00\x01") == TYPE_BLOB

    def test_unknown_type_still_falls_back_to_var_string(self):
        """An exotic value (e.g. a JSON wrapper) must not break the response."""
        from app.proxy.protocol import TYPE_VAR_STRING

        class Exotic:
            pass

        assert self._type_of(Exotic()) == TYPE_VAR_STRING


class TestUserVariableSubstitutionIsWiredIn:
    """The read path must be connected, not merely available — NOVA-25.

    The unit tests for ``substitute_user_variables`` prove the function works;
    these prove the executor *calls* it. That distinction is the whole defect:
    the state was written and nothing read it.
    """

    async def test_set_then_select_reaches_the_engine_substituted(self, monkeypatch):
        fake = FakeQueryService()
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        # A two-statement script is split by the proxy, so the SELECT is
        # executed on its own with the stored value spliced in.
        result = await executor.execute(
            "SET @x = 'QA_MARKER_12345'; SELECT @x AS val",
            username="u",
            connection=object(),
        )

        assert result.error is None
        assert len(fake.calls) == 1
        assert fake.last_sql == "SELECT 'QA_MARKER_12345' AS val"

    async def test_substitution_applies_to_a_separate_later_query(self, monkeypatch):
        fake = FakeQueryService()
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        await executor.execute("SET @threshold = 100", username="u", connection=object())
        await executor.execute(
            "SELECT * FROM t WHERE amount > @threshold", username="u", connection=object()
        )

        assert fake.last_sql == "SELECT * FROM t WHERE amount > 100"

    async def test_stage_reference_is_not_substituted(self, monkeypatch):
        fake = FakeQueryService()
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        await executor.execute(
            "SELECT * FROM @products.products_new.csv", username="u", connection=object()
        )

        assert fake.last_sql == "SELECT * FROM @products.products_new.csv"

    async def test_a_literal_that_looks_like_a_reference_is_untouched(self, monkeypatch):
        fake = FakeQueryService()
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        await executor.execute("SET @x = 1", username="u", connection=object())
        await executor.execute("SELECT '@x' AS s", username="u", connection=object())

        assert fake.last_sql == "SELECT '@x' AS s"


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
