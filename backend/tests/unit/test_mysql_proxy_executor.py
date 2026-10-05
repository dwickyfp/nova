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
from app.proxy.session import SessionState, handle_set_statement


class FakeQueryService:
    """Records calls and returns queued results."""

    def __init__(self, results: list[QueryResult] | None = None) -> None:
        self.calls: list[dict] = []
        self.preflights: list[list[str]] = []
        self._results = list(results or [QueryResult()])

    async def preflight_script(self, statements, **kwargs) -> QueryResult | None:
        self.preflights.append(list(statements))
        return None

    async def execute_statements(self, **kwargs) -> list[QueryResult]:
        """Each call runs one statement; queued results are consumed in order."""
        self.calls.append(kwargs)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        return [result]

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


async def test_unsupported_frontend_capability_has_a_mysql_refusal_code(monkeypatch):
    fake = FakeQueryService(
        [
            QueryResult(
                error="CREATE ML_MODEL requires an API session", error_code="capability_unsupported"
            )
        ]
    )
    _patch_service(monkeypatch, fake)
    result = await ProxyQueryExecutor(SessionState()).execute(
        "CREATE ML_MODEL m TYPE=REGRESSION TARGET=y AS SELECT 1 AS y",
        username="analyst",
        connection=object(),
    )
    assert result.error_code == 1235
    assert "requires an API session" in result.error


async def test_user_variable_expression_is_evaluated_once_as_the_authenticated_caller(monkeypatch):
    fake = FakeQueryService([QueryResult(columns=["nova_user_variable"], rows=[[2]])])
    _patch_service(monkeypatch, fake)
    session = SessionState(
        database="db", active_role="reader", security_context_version=7, user_variables={"x": "1"}
    )
    connection = object()
    executor = ProxyQueryExecutor(session)
    result = await executor.execute(
        "SET @x=@x+1", username="alice", connection=connection, session_id="session"
    )
    assert result.is_ok and session.user_variables["x"] == "2"
    assigned = fake.calls[0]
    assert assigned["sql"] == "SELECT (1+1) AS nova_user_variable"
    assert assigned["username"] == "alice" and assigned["connection"] is connection
    assert assigned["role"] == "reader" and assigned["security_context_version"] == 7
    assert assigned["session_id"] == "session"
    await executor.execute("SELECT @x=@x", username="alice", connection=connection)
    assert fake.last_sql == "SELECT 2=2" and len(fake.calls) == 2


async def test_failed_variable_expression_preserves_the_old_value(monkeypatch):
    fake = FakeQueryService([QueryResult(error="Unknown column missing")])
    _patch_service(monkeypatch, fake)
    session = SessionState(user_variables={"x": "1"})
    result = await ProxyQueryExecutor(session).execute(
        "SET @x=missing+1", username="alice", connection=object()
    )
    assert result.error and session.user_variables == {"x": "1"}


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

    async def test_use_updates_context_after_engine_validation(self, fake_service):
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        result = await executor.execute("USE NOVA_DEMO", username="u", connection=object())

        assert result.is_ok
        assert session.database == "NOVA_DEMO"
        assert fake_service.last_sql == "USE NOVA_DEMO"

    async def test_failed_use_preserves_previous_database(self, monkeypatch):
        fake = FakeQueryService([QueryResult(error="Unknown database")])
        _patch_service(monkeypatch, fake)
        session = SessionState(database="existing")
        result = await ProxyQueryExecutor(session).execute(
            "USE missing", username="u", connection=object()
        )
        assert result.error == "Unknown database"
        assert session.database == "existing"

    async def test_script_session_changes_apply_to_later_statements(self, fake_service):
        session = SessionState(database="first")
        result = await ProxyQueryExecutor(session).execute(
            "SET @x = 1; USE second; SELECT @x; SET @x = 2; SELECT @x",
            username="u",
            connection=object(),
        )
        assert result.error is None
        assert [(call["sql"], call["database"]) for call in fake_service.calls] == [
            ("USE second", "first"),
            ("SELECT 1", "second"),
            ("SELECT 2", "second"),
        ]
        assert len(result.preceding) == 4
        assert session.database == "second"

    async def test_script_refusal_runs_nothing(self, monkeypatch):
        class Refusing(FakeQueryService):
            async def preflight_script(self, statements, **kwargs):
                return QueryResult(error="Invalid SQL: 1:7 Unexpected or missing SQL token")

        fake = _patch_service(monkeypatch, Refusing())
        session = SessionState(database="existing", user_variables={"x": "0"})
        result = await ProxyQueryExecutor(session).execute(
            "SET @x = 1; USE other; SELEC 2", username="u", connection=object()
        )
        assert result.error and result.preceding == []
        assert fake.calls == []
        assert session.database == "existing"
        assert session.user_variables == {"x": "0"}

    async def test_script_stops_at_the_first_error(self, monkeypatch):
        fake = _patch_service(
            monkeypatch,
            FakeQueryService(
                [QueryResult(), QueryResult(error="SQL error: (5502) no t", engine_error_code=5502)]
            ),
        )
        session = SessionState(user_variables={"x": "0"})
        result = await ProxyQueryExecutor(session).execute(
            "INSERT INTO a VALUES (1); SELECT * FROM t; SET @x = 9",
            username="u",
            connection=object(),
        )
        assert result.error_code == 5502
        assert len(fake.calls) == 2
        assert session.user_variables["x"] == "0"

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
    async def test_engine_metadata_survives_all_null_and_empty_results(self, monkeypatch):
        fake = FakeQueryService(
            [
                QueryResult(
                    columns=["integer", "decimal"],
                    rows=[[None, None]],
                    column_types=("(3, 11, 0)", "(246, 20, 4)"),
                )
            ]
        )
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())
        result = await executor.execute("SELECT NULL", username="alice", connection=object())
        assert [column.type_code for column in result.columns] == [3, 246]
        assert result.columns[1].decimals == 4
        fake._results[0].rows = []
        result = await executor.execute(
            "SELECT NULL WHERE FALSE", username="alice", connection=object()
        )
        assert [column.type_code for column in result.columns] == [3, 246]

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

    async def test_each_statement_reports_its_own_affected_rows(self, monkeypatch):
        fake = FakeQueryService([QueryResult(affected_rows=2), QueryResult(affected_rows=3)])
        _patch_service(monkeypatch, fake)
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute(
            "DELETE FROM a; DELETE FROM b", username="u", connection=object()
        )

        assert result.ok_affected == 3
        assert [part.ok_affected for part in result.preceding] == [2]

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


class TestMySQLClientCompatibility:
    """Behaviour a MySQL client relies on that the proxy must not change."""

    async def test_destructive_statements_run_without_a_confirmation_exchange(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService([QueryResult(affected_rows=1)]))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute(
            "DELETE FROM t WHERE id = 1", username="u", connection=object()
        )

        assert result.error is None
        assert fake.calls[-1]["confirm_destructive"] is True

    async def test_quoted_semicolons_reach_the_pipeline_unchanged(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        executor = ProxyQueryExecutor(SessionState())

        await executor.execute('SELECT "a;b", \'c\\\';d\'', username="u", connection=object())

        assert fake.last_sql == 'SELECT "a;b", \'c\\\';d\''

    async def test_a_trailing_line_comment_cannot_swallow_the_next_statement(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService([QueryResult(), QueryResult()]))
        executor = ProxyQueryExecutor(SessionState())

        await executor.execute("SELECT 1 -- note\n; SELECT 2", username="u", connection=object())

        assert [call["sql"] for call in fake.calls] == ["SELECT 1 -- note", "SELECT 2"]

    async def test_results_over_the_row_limit_are_refused_not_truncated(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "PROXY_MAX_ROWS", 2)
        truncated = QueryResult(columns=["id"], rows=[[1], [2]], truncated=True)
        fake = _patch_service(monkeypatch, FakeQueryService([truncated]))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT id FROM t", username="u", connection=object())

        assert fake.calls[-1]["max_rows"] == 2
        assert result.rows == []
        assert result.error_code == 1235
        assert "limit of 2 rows" in result.error

    async def test_engine_error_numbers_reach_the_client(self, monkeypatch):
        failed = QueryResult(
            error="SQL error: (1049) Unknown database 'missing'",
            error_code="execution_error",
            engine_error_code=1049,
        )
        _patch_service(monkeypatch, FakeQueryService([failed]))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute(
            "SELECT * FROM missing.t", username="u", connection=object()
        )

        assert result.error_code == 1049
        assert result.error == "Unknown database 'missing'"

    async def test_client_library_error_numbers_are_not_forwarded(self, monkeypatch):
        failed = QueryResult(error="Connection error: lost", engine_error_code=2013)
        _patch_service(monkeypatch, FakeQueryService([failed]))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1", username="u", connection=object())

        assert result.error_code == 1064

    async def test_multi_statement_results_keep_statement_order(self, monkeypatch):
        first = QueryResult(columns=["a"], rows=[[1]])
        second = QueryResult(columns=["b"], rows=[[2]])
        _patch_service(monkeypatch, FakeQueryService([first, second]))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SELECT 1 a; SELECT 2 b", username="u", connection=object())

        assert [part.rows for part in result.preceding] == [[[1]]]
        assert result.rows == [[2]]

    async def test_local_assignments_answer_before_engine_results(self, monkeypatch):
        result_set = QueryResult(columns=["x"], rows=[[5]])
        fake = _patch_service(monkeypatch, FakeQueryService([result_set]))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute("SET @x = 5; SELECT @x", username="u", connection=object())

        assert fake.last_sql == "SELECT 5"
        assert len(result.preceding) == 1 and result.preceding[0].is_ok
        assert result.rows == [[5]]

    async def test_error_ends_the_response_after_completed_statements(self, monkeypatch):
        results = [QueryResult(affected_rows=1), QueryResult(error="SQL error: boom")]
        _patch_service(monkeypatch, FakeQueryService(results))
        executor = ProxyQueryExecutor(SessionState())

        result = await executor.execute(
            "INSERT INTO t VALUES (1); INSERT INTO u VALUES (2)", username="u", connection=object()
        )

        assert [part.ok_affected for part in result.preceding] == [1]
        assert result.error is not None

    async def test_catalog_switch_stops_reselecting_the_old_database(self, monkeypatch):
        _patch_service(monkeypatch, FakeQueryService())
        session = SessionState()
        session.set_database("audit_db")
        executor = ProxyQueryExecutor(session)

        result = await executor.execute("SET CATALOG hive", username="u", connection=object())

        assert result.error is None
        assert session.database is None

    async def test_catalog_switch_inside_a_script_applies_in_order(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        session = SessionState()
        session.set_database("audit_db")
        executor = ProxyQueryExecutor(session)

        result = await executor.execute(
            "SET CATALOG hive; SELECT 1", username="u", connection=object()
        )

        assert result.error is None
        assert [call["database"] for call in fake.calls] == ["audit_db", None]


class TestTypedUserVariables:
    """ARRAY/MAP/STRUCT/JSON values keep their type on the engine session."""

    async def test_complex_value_is_kept_on_the_engine_session(self, monkeypatch):
        evaluated = QueryResult(
            columns=["nova_user_variable"], rows=[["[1,2,3]"]], column_types=("(254, None, 60)",)
        )

        class Sequenced(FakeQueryService):
            async def execute_statements(self, **kwargs):
                self.calls.append(kwargs)
                return [evaluated] if len(self.calls) == 1 else [QueryResult()]

        fake = _patch_service(monkeypatch, Sequenced())
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        result = await executor.execute("SET @a = [1,2,3]", username="u", connection=object())
        await executor.execute("SELECT array_length(@a)", username="u", connection=object())

        assert result.error is None
        assert [call["sql"] for call in fake.calls] == [
            "SELECT ([1,2,3]) AS nova_user_variable",
            "SET @a = [1,2,3]",
            "SELECT array_length(@a)",
        ]
        assert "a" in session.engine_variables and "a" not in session.user_variables

    async def test_varchar_value_is_still_substituted(self, monkeypatch):
        evaluated = QueryResult(
            columns=["nova_user_variable"], rows=[["[1,2,3]"]], column_types=("(253, None, 64)",)
        )
        fake = _patch_service(monkeypatch, FakeQueryService([evaluated]))
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        await executor.execute("SET @a = concat('[1,', '2,3]')", username="u", connection=object())
        await executor.execute("SELECT @a", username="u", connection=object())

        assert fake.last_sql == "SELECT '[1,2,3]'"
        assert not session.engine_variables

    async def test_a_literal_reassignment_takes_back_ownership(self):
        session = SessionState()
        session.engine_variables.add("a")

        handle_set_statement("SET @a = 5", session)

        assert session.user_variables["a"] == "5"
        assert "a" not in session.engine_variables


class TestStreamedResults:
    """Engine rows reach the sink batch by batch, in statement order."""

    class StreamingService(FakeQueryService):
        def __init__(self, batches, *, columns=("id",)):
            super().__init__()
            self._batches = batches
            self._columns = list(columns)

        async def execute_statements(self, **kwargs):
            self.calls.append(kwargs)
            sink = kwargs["row_sink"]
            await sink.begin(self._columns, tuple("(8, 20, 0)" for _ in self._columns))
            for batch in self._batches:
                await sink.rows(batch)
            return [QueryResult(columns=self._columns, streamed=True)]

    async def test_rows_stream_into_one_result_set(self, monkeypatch):
        _patch_service(monkeypatch, self.StreamingService([[[1], [2]], [[3]]]))
        result = await ProxyQueryExecutor(SessionState()).execute(
            "SELECT id FROM t", username="u", connection=object()
        )
        assert result.rows == [[1], [2], [3]]
        assert [column.name for column in result.columns] == ["id"]

    async def test_row_cap_ends_with_an_error_after_partial_rows(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "PROXY_MAX_ROWS", 2)
        _patch_service(monkeypatch, self.StreamingService([[[1], [2]], [[3]]]))
        result = await ProxyQueryExecutor(SessionState()).execute(
            "SELECT id FROM t", username="u", connection=object()
        )
        assert result.error_code == 1235 and "limit of 2 rows" in result.error

    async def test_hidden_databases_are_filtered_while_streaming(self, monkeypatch):
        service = self.StreamingService(
            [[["NOVA_SYSTEM"], ["sales"]], [["information_schema"], ["ops"]]],
            columns=("Database",),
        )
        _patch_service(monkeypatch, service)
        result = await ProxyQueryExecutor(SessionState()).execute(
            "SHOW DATABASES LIKE '%'", username="u", connection=object()
        )
        assert result.rows == [["sales"], ["ops"]]
        assert service.last_sql == "SHOW DATABASES LIKE '%'"


class TestSQLPreparedStatements:
    async def test_prepare_execute_deallocate(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        prepared = await executor.execute(
            "PREPARE s FROM 'SELECT * FROM t WHERE id = ? AND note = ?'",
            username="u",
            connection=object(),
        )
        await executor.execute("SET @a = 7", username="u", connection=object())
        await executor.execute("SET @b = 'x''y'", username="u", connection=object())
        run = await executor.execute("EXECUTE s USING @a, @b", username="u", connection=object())
        dropped = await executor.execute("DEALLOCATE PREPARE s", username="u", connection=object())

        assert prepared.is_ok and run.error is None and dropped.is_ok
        assert fake.last_sql == "SELECT * FROM t WHERE id = 7 AND note = 'x''y'"
        assert session.prepared == {}

    async def test_wrong_argument_count_is_refused(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        executor = ProxyQueryExecutor(SessionState())
        await executor.execute("PREPARE s FROM 'SELECT ?'", username="u", connection=object())

        result = await executor.execute("EXECUTE s", username="u", connection=object())

        assert result.error_code == 1210
        assert fake.calls == []

    async def test_unknown_statement_is_refused(self, monkeypatch):
        _patch_service(monkeypatch, FakeQueryService())
        result = await ProxyQueryExecutor(SessionState()).execute(
            "EXECUTE missing", username="u", connection=object()
        )
        assert result.error_code == 1243

    async def test_placeholders_inside_literals_are_not_parameters(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        executor = ProxyQueryExecutor(SessionState())
        await executor.execute(
            "PREPARE s FROM 'SELECT ''?'' AS q, ? AS v'", username="u", connection=object()
        )
        await executor.execute("SET @v = 1", username="u", connection=object())
        await executor.execute("EXECUTE s USING @v", username="u", connection=object())
        assert fake.last_sql == "SELECT '?' AS q, 1 AS v"


class TestClientTransactions:
    async def test_statements_inside_a_transaction_reuse_the_engine_session(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        session = SessionState()
        executor = ProxyQueryExecutor(session)

        for sql in ("BEGIN", "INSERT INTO t VALUES (1)", "COMMIT", "SELECT 1"):
            result = await executor.execute(sql, username="u", connection=object())
            assert result.error is None

        assert [call["client_transaction"] for call in fake.calls] == [False, True, True, False]
        assert session.in_transaction is False

    async def test_failed_begin_does_not_open_a_transaction(self, monkeypatch):
        _patch_service(monkeypatch, FakeQueryService([QueryResult(error="SQL error: (5305) no")]))
        session = SessionState()
        await ProxyQueryExecutor(session).execute("BEGIN", username="u", connection=object())
        assert session.in_transaction is False

    async def test_rollback_closes_the_transaction_even_when_it_fails(self, monkeypatch):
        _patch_service(monkeypatch, FakeQueryService([QueryResult(error="SQL error: (1) no")]))
        session = SessionState(in_transaction=True)
        await ProxyQueryExecutor(session).execute("ROLLBACK", username="u", connection=object())
        assert session.in_transaction is False

    async def test_role_changes_are_refused_inside_a_transaction(self, monkeypatch):
        fake = _patch_service(monkeypatch, FakeQueryService())
        session = SessionState(in_transaction=True)
        result = await ProxyQueryExecutor(session).execute(
            "SET ROLE analyst", username="u", connection=object()
        )
        assert result.error_code == 1235
        assert fake.calls == []
