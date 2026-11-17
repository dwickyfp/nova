from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import NativeStatement, ParameterizedQueryStatement
from app.sql_frontend.context import ExecutionContext, PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.fingerprint import fingerprint
from app.sql_frontend.parameters import (
    BoundParameters,
    ParameterSpec,
    driver_parameters,
    parameter_slots,
)
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.read_queries import validate_read_query


def bound(value, kind="string", **kwargs):
    if kind == "decimal":
        kwargs = {"precision": 38, "scale": 12, **kwargs}
    return BoundParameters((ParameterSpec("value", kind, **kwargs),), (("value", value),))


@pytest.mark.parametrize(
    "kind,value",
    [
        ("string", "O'Reilly; DROP TABLE t; -- \\ % :other"),
        ("boolean", False),
        ("integer", -(2**63)),
        ("integer", 2**63 - 1),
        ("decimal", Decimal("12345678901234567890123456.123456789012")),
        ("decimal", Decimal("0.00000000000000000000000000000000000001")),
        ("date", date(2024, 2, 29)),
        ("timestamp", datetime(2024, 11, 3, 6, 30, tzinfo=UTC)),
    ],
)
def test_scalar_values_remain_separate_from_sql_and_diagnostics(kind, value):
    scale = 38 if kind == "decimal" and value == Decimal("1E-38") else 12
    bindings = bound(value, kind, **({"scale": scale} if kind == "decimal" else {}))
    sql, args = driver_parameters("SELECT :value AS result", bindings, "UTC")
    sql_type = {"string": "VARCHAR(65536)", "boolean": "BOOLEAN", "integer": "BIGINT",
                "decimal": f"DECIMAL(38,{scale})", "date": "DATE", "timestamp": "DATETIME"}[kind]
    assert sql == f"SELECT CAST(%s AS {sql_type}) AS result"
    expected = value.replace(tzinfo=None) if isinstance(value, datetime) else (
        format(value, f".{scale}f") if isinstance(value, Decimal) else value
    )
    assert args == (expected,)
    assert "values=" not in repr(bindings)
    assert "parameters=" not in repr(ExecutionContext("alice", parameters=bindings))


@pytest.mark.parametrize(
    "kind,value",
    [
        ("string", 123),
        ("string", "x" * 65537),
        ("string", "\ud800"),
        ("integer", True),
        ("integer", 1.0),
        ("integer", "1"),
        ("integer", 2**63),
        ("integer", -(2**63) - 1),
        ("boolean", 1),
        ("boolean", "false"),
        ("decimal", 1.1),
        ("decimal", Decimal("NaN")),
        ("decimal", Decimal("Infinity")),
        ("decimal", Decimal("1E38")),
        ("decimal", Decimal("1E-39")),
        ("date", datetime(2024, 1, 1)),
        ("date", "2024-01-01"),
        ("timestamp", datetime(2024, 1, 1)),
        ("integer", None),
    ],
)
def test_invalid_values_are_rejected_without_echo(kind, value):
    with pytest.raises(SemanticError) as error:
        bound(value, kind)
    assert "Parameter" in str(error.value) or "parameter" in str(error.value)
    assert "123456789" not in str(error.value)


def test_null_lists_repetition_and_percent_escaping():
    assert driver_parameters("SELECT :value AS n", bound(None, nullable=True), "UTC")[1] == (None,)
    bindings = bound((1, None, 3), "integer", multiple=True, nullable=True)
    sql, args = driver_parameters(
        "SELECT id % 10 FROM t WHERE id IN (:value) OR id NOT IN (:value) AND s LIKE 'a%'",
        bindings,
        "UTC",
    )
    assert sql == (
        "SELECT id %% 10 FROM t WHERE id IN (CAST(%s AS BIGINT), CAST(%s AS BIGINT), "
        "CAST(%s AS BIGINT)) OR id NOT IN (CAST(%s AS BIGINT), CAST(%s AS BIGINT), "
        "CAST(%s AS BIGINT)) AND s LIKE 'a%%'"
    )
    assert args == (1, None, 3, 1, None, 3)


@pytest.mark.parametrize("value", [(), tuple(range(501)), [1, 2], ((1,),)])
def test_list_bounds_and_scalar_items(value):
    with pytest.raises(SemanticError):
        bound(value, "integer", multiple=True)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT :value AS result",
        "SELECT 1 WHERE id = :value",
        "SELECT 1 WHERE id IN (:value, 2)",
        "SELECT 1 WHERE id IN (coalesce(:value, 2))",
        "SELECT 1 WHERE id IN ((:value))",
        "SELECT 1 WHERE id IN (:value + 1)",
    ],
)
def test_list_parameters_cannot_change_expression_structure(sql):
    with pytest.raises(SemanticError, match="sole operand"):
        driver_parameters(sql, bound((1, 2), "integer", multiple=True), "UTC")


@pytest.mark.parametrize("name", ["", "_x", "x-y", "x" * 65, "é", "a.b"])
def test_invalid_names(name):
    with pytest.raises(SemanticError):
        ParameterSpec(name, "string")


@pytest.mark.parametrize("precision,scale", [
    (None, None), (38, None), (None, 2), (0, 0), (39, 2), (5, 6),
    (10, -1), (True, 0), (10, False), (10.0, 2), (10, 2.0),
])
def test_decimal_declarations_require_exact_bounded_precision_and_scale(precision, scale):
    with pytest.raises(SemanticError, match="precision and scale"):
        ParameterSpec("amount", "decimal", precision=precision, scale=scale)


@pytest.mark.parametrize("value", [Decimal("0E-99999"), Decimal("-0E+99999")])
def test_zero_exponent_cannot_expand_the_declared_decimal_output(value):
    sql, args = driver_parameters(
        "SELECT :value AS result", bound(value, "decimal", precision=5, scale=2), "UTC",
    )
    assert sql == "SELECT CAST(%s AS DECIMAL(5,2)) AS result"
    assert args in (("0.00",), ("-0.00",))


def test_decimal_bounds_refuse_rounding_and_overflow_but_accept_insignificant_zeros():
    for value in (Decimal("1000"), Decimal("-1000"), Decimal("0.001")):
        with pytest.raises(SemanticError, match="bounds"):
            bound(value, "decimal", precision=5, scale=2)
    for value in (Decimal("999.99"), Decimal("-999.99"), Decimal("1.2300"), Decimal("0")):
        bound(value, "decimal", precision=5, scale=2)
    bound(Decimal("0"), "decimal", precision=38, scale=38)
    with pytest.raises(SemanticError, match="Only decimal"):
        ParameterSpec("n", "integer", precision=5, scale=2)


def test_string_byte_bound_matches_the_driver_cast_without_multibyte_truncation():
    bound("界" * 21845)
    with pytest.raises(SemanticError, match="UTF-8 byte limit"):
        bound("界" * 21846)


def test_names_values_and_declarations_are_exact_and_immutable():
    spec = ParameterSpec("value", "integer")
    for specs, values in [
        ((), ()),
        ((spec, spec), (("value", 1), ("value", 2))),
        ((spec,), (("other", 1),)),
        ([spec], (("value", 1),)),
        ((spec,), [["value", 1]]),
        ((spec,), (("value", (1,)),)),
    ]:
        with pytest.raises(SemanticError):
            BoundParameters(specs, values)
    with pytest.raises(SemanticError, match="byte limit"):
        bound(tuple("x" * 65536 for _ in range(17)), multiple=True)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT :missing AS n",
        "SELECT :VALUE AS n",
        "SELECT : value AS n",
        "SELECT :`value` AS n",
    ],
)
def test_undeclared_unused_noncanonical_parameters_are_refused(sql):
    with pytest.raises(SemanticError):
        parameter_slots(parse_statement(sql), bound("private").specs)


def test_parameter_reference_count_is_bounded():
    with pytest.raises(SemanticError, match="reference limit"):
        parameter_slots(
            parse_statement("SELECT " + ",".join(":value AS n" for _ in range(257))),
            bound(1, "integer").specs,
        )


@pytest.mark.parametrize(
    "sql",
    ["SELECT :value", "SELECT coalesce(:value, 'x')", "SELECT (SELECT :value) AS n"],
)
def test_projection_labels_cannot_echo_values(sql):
    with pytest.raises(SemanticError, match="column alias"):
        driver_parameters(sql, bound("private"), "UTC")


def test_timezone_conversion_preserves_instant_at_dst_fold():
    binding = bound(datetime(2024, 11, 3, 6, 30, tzinfo=UTC), "timestamp")
    sql = "SELECT :value AS moment"
    assert driver_parameters(sql, binding, "America/New_York")[1] == (datetime(2024, 11, 3, 1, 30),)
    assert driver_parameters(sql, binding, "+07:00")[1] == (datetime(2024, 11, 3, 13, 30),)
    assert driver_parameters(sql, binding, "-05:30")[1] == (datetime(2024, 11, 3, 1, 0),)
    for zone in ["missing/zone", "+14:01", "+07:99"]:
        with pytest.raises(SemanticError, match="timezone"):
            driver_parameters(sql, binding, zone)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT map{'k':value} FROM t",
        "SELECT [1,2][1:2]",
        "SELECT ':value' AS x /* :ignored */ -- :also_ignored\n",
        "SELECT ? AS x",
    ],
)
def test_existing_colon_literals_comments_maps_slices_and_positional_parameters(sql):
    assert type(ast_builders.build(parse_statement(sql))) is NativeStatement


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM t",
        "INSERT INTO t SELECT 1",
        "EXPLAIN SELECT 1",
        "SELECT @x",
        "SELECT @@time_zone",
        "SELECT * FROM @files.data.csv",
        "SELECT sleep(1)",
        "SELECT http_get('https://example.invalid')",
        "SELECT my_udf(1)",
        "SELECT db.abs(1)",
        "SELECT password('x')",
        "SELECT user()",
        "SELECT * FROM TABLE(generate_series(1,3))",
        "SELECT * FROM unnest([1,2])",
        "SELECT 1 INTO OUTFILE 's3://bucket/file'",
        "SELECT 1 LIMIT ?",
        "SELECT /*+ SET_VAR(enable_profile=true) */ 1",
        "SELECT 1; SELECT 2",
        "WITH RECURSIVE c AS (SELECT 1) SELECT * FROM c",
    ],
)
def test_uncertain_or_effectful_queries_fail_closed(sql):
    with pytest.raises(ValueError):
        validate_read_query(parse_statement(sql))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, SUM(amount) AS total FROM sales GROUP BY id ORDER BY total LIMIT 10",
        "WITH s AS (SELECT id FROM sales) SELECT id FROM s WHERE id > :value",
        "SELECT date_trunc('month', d), LAG(v) OVER (ORDER BY d) FROM sales",
    ],
)
def test_composable_read_queries(sql):
    validate_read_query(parse_statement(sql))


async def test_plan_is_value_free_and_declarations_do_not_leak_across_planning_contexts():
    sql = "SELECT :value AS result"
    statement = ast_builders.build(parse_statement(sql))
    assert type(statement) is ParameterizedQueryStatement
    values = bound("private-canary")
    planner = SQLPlanner()
    plan = await planner.plan(statement, PlanningContext(parameter_specs=values.specs))
    assert plan.engine_sql == sql and plan.parameter_specs == values.specs
    assert "private-canary" not in str(asdict(plan))
    with pytest.raises(SemanticError):
        await planner.plan(statement, PlanningContext())
    assert not fingerprint(sql).replay_eligible


@pytest.fixture
def service(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "caller-password")
    service = QueryService()
    service._repo = AsyncMock()
    service._repo.execute_as_user.return_value = QueryResult(columns=["n"], rows=[[1]])
    return service, audit


async def test_query_service_preserves_actor_and_separates_parameters_from_telemetry(
    service, monkeypatch
):
    from app.modules.query_autopilot.telemetry import collector

    query, audit = service
    observe, profile = Mock(), Mock(return_value=True)
    monkeypatch.setattr(collector, "observe", observe)
    monkeypatch.setattr(collector, "request_profile", profile)
    values = bound("private-canary")
    result = await query.execute(
        "SELECT :value AS n",
        "alice",
        "enc",
        role="analyst",
        max_rows=10,
        session_id="session",
        security_context_version=8,
        parameters=values,
    )
    assert result.error is None and result.rows == [[1]]
    call = query._repo.execute_as_user.await_args.kwargs
    assert call["username"] == "alice" and call["role"] == "analyst"
    assert call["parameters"] is values and call["sql"] == "SELECT :value AS n"
    assert "private-canary" not in str(audit.await_args_list)
    assert "parameters" not in observe.call_args.kwargs["kwargs"]
    assert "private-canary" not in str(observe.call_args)
    profile.assert_not_called()


@pytest.mark.parametrize(
    "sql,options",
    [
        ("SELECT :value AS n", {}),
        ("SELECT :value AS n", {"max_rows": 0}),
        ("SELECT :value AS n", {"max_rows": 100001}),
        ("SELECT :value AS n", {"max_rows": 1, "client_transaction": True}),
        ("SELECT :value AS n", {"max_rows": 1, "connection": object()}),
        ("SELECT :value AS n", {"row_sink": object()}),
        ("SELECT 1", {"max_rows": 1}),
        ("SELECT sleep(:value) AS n", {"max_rows": 1}),
        ("INSERT INTO t SELECT :value AS n", {"max_rows": 1}),
    ],
)
async def test_invalid_bound_execution_never_reaches_engine(service, sql, options):
    query, audit = service
    result = await query.execute(
        sql, "alice", "enc", role="analyst", parameters=bound(1, "integer"), **options
    )
    assert result.error
    query._repo.execute_as_user.assert_not_awaited()
    audit.assert_awaited_once()


async def test_normal_query_service_rejects_unbound_named_parameters(service):
    query, audit = service
    result = await query.execute("SELECT :value AS n", "alice", "enc", role="analyst")
    assert result.error
    query._repo.execute_as_user.assert_not_awaited()
    audit.assert_awaited_once()


async def test_bound_stream_preserves_caller_sink_values_and_limit(service):
    from types import SimpleNamespace

    query, audit = service
    sink = SimpleNamespace(begin=AsyncMock(), rows=AsyncMock())
    query._repo.execute_as_user.return_value = QueryResult(
        columns=["n"], rows=[], streamed=True, row_count=1,
    )
    parameters = bound(2**63 - 1, "integer")
    result = await query.execute(
        "SELECT :value AS n", "alice", "enc", role="analyst", max_rows=1,
        session_id="session", security_context_version=8, parameters=parameters, row_sink=sink,
    )
    assert result.error is None and result.streamed and result.rows == []
    call = query._repo.execute_as_user.await_args.kwargs
    assert call["row_sink"] is sink and call["parameters"] is parameters
    assert call["max_rows"] == 1 and call["username"] == "alice" and call["role"] == "analyst"
    assert str(2**63 - 1) not in str(audit.await_args_list)


def test_repeated_parameters_cannot_expand_past_execution_budget():
    sql = "SELECT 1 WHERE " + " OR ".join("id IN (:value)" for _ in range(9))
    with pytest.raises(SemanticError, match="execution limit"):
        driver_parameters(sql, bound(tuple(range(500)), "integer", multiple=True), "UTC")
    sql = "SELECT " + ", ".join(f":value AS n{i}" for i in range(17))
    with pytest.raises(SemanticError, match="execution limit"):
        driver_parameters(sql, bound("x" * 65536), "UTC")


@pytest.mark.parametrize("failure", [None, "database", "cancel", "profile_setup"])
async def test_authenticated_driver_binding_failure_redaction_and_cancellation(failure):
    import asyncio

    import asyncmy

    from app.core.exceptions import StarRocksError
    from app.modules.query.repository import QueryRepository

    calls = []
    secret = "private-canary'; SELECT sleep(99); --"

    class Cursor:
        description = [("n", 253, None, None, None, None, True)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, sql, args=None):
            calls.append((sql, args))
            if failure == "profile_setup" and sql == "SET enable_profile = false":
                raise asyncmy.errors.DatabaseError(1227, "setup denied")
            if args is not None:
                if failure == "database":
                    raise asyncmy.errors.DatabaseError(1064, f"bad {secret}")
                if failure == "cancel":
                    raise asyncio.CancelledError

        async def fetchmany(self, _):
            return [{"n": 1}]

    class Connection:
        closed = False

        def cursor(self, *_):
            return Cursor()

        def close(self):
            self.closed = True

        async def select_db(self, database):
            calls.append(("database", database))

    conn = Connection()
    pending = QueryRepository._execute_on(
        conn, "SELECT :value AS n", role="analyst", database="sales", max_rows=1,
        parameters=bound(secret), start=0,
    )
    if failure in {"database", "profile_setup"}:
        with pytest.raises(StarRocksError) as error:
            await pending
        assert secret not in str(error.value)
        assert error.value.engine_code == (1064 if failure == "database" else 1227)
        assert error.value.__suppress_context__
    elif failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert conn.closed
    else:
        result = await pending
        assert result.rows == [[1]] and result.executed_sql == "SELECT :value AS n"
    assert calls == [
        ("SET ROLE analyst", None), ("database", "sales"), ("SET enable_profile = false", None),
    ] + ([] if failure == "profile_setup" else [
        ("SELECT CAST(%s AS VARCHAR(65536)) AS n", (secret,))
    ])


async def test_repository_cannot_bind_inputs_on_a_borrowed_session():
    from app.core.exceptions import StarRocksError
    from app.modules.query.repository import QueryRepository

    with pytest.raises(StarRocksError, match="managed session"):
        await QueryRepository().execute_as_user(
            "SELECT :value AS n", "alice", "", role="analyst", connected=object(),
            parameters=bound("private"),
        )
