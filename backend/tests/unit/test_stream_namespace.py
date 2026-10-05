from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.modules.streams.namespace import StreamName
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import NativeStatement, StreamStatement
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SQLFrontendError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import NovaActionPlan
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.streams import parse_stream_name


@pytest.mark.parametrize("name", ["s", "analytics.s", "analytics.default.s"])
def test_aliases_have_one_identity(name):
    assert parse_stream_name(name, "analytics") == StreamName("analytics", "s")


@pytest.mark.parametrize("name,expected", [
    ("`a.b`.default.`c``d`", StreamName("a.b", "c`d")),
    ("`Analytics`.`Orders`", StreamName("Analytics", "Orders")),
    ("db.123abc", StreamName("db", "123abc")),
])
def test_identifier_fidelity(name, expected):
    assert parse_stream_name(name) == expected
    assert parse_stream_name(expected.qualified) == expected


@pytest.mark.parametrize("name", [
    "s", "db.other.s", "default_catalog.db.s", "cat.db.default.s", "db..s",
    "db.s; SELECT 1", "", "`db`.``", "db.s extra",
])
def test_reject_ambiguous_or_invalid_scope(name):
    with pytest.raises(SQLFrontendError):
        parse_stream_name(name)


def test_names_are_database_scoped():
    assert parse_stream_name("s", "one") != parse_stream_name("s", "two")


@pytest.mark.parametrize("name", ["s", "db.s", "db.default.s"])
@pytest.mark.parametrize("sql,operation", [
    ("CREATE STREAM IF NOT EXISTS db.default.s ON TABLE orders APPEND_ONLY=TRUE", "create"),
    ("CREATE STREAM db.s ON TABLE orders APPEND_ONLY=TRUE", "create"),
    ("DROP STREAM db.s", "drop"),
    ("DROP STREAM IF EXISTS db.s", "drop"),
    ("DESCRIBE STREAM db.default.s", "describe"),
    ("DESC STREAM db.s", "describe"),
    ("SHOW STREAM STATUS db.s", "status"),
    ("SHOW STREAM BACKLOG db.default.s", "backlog"),
    ("SHOW STREAMS", "list"),
])
async def test_typed_planning_freezes_scope_without_io(sql, operation, name):
    sql = sql.replace("db.default.s", name).replace("db.s", name)
    statement = ast_builders.build(parse_statement(sql))
    assert isinstance(statement, StreamStatement)
    binder = AsyncMock(side_effect=AssertionError("planning requested I/O"))
    context = PlanningContext(database="session", binder=binder)
    plan = await SQLPlanner().plan(statement, context, source_key=7)
    assert isinstance(plan, NovaActionPlan)
    assert plan.payload.source_key == 7
    assert plan.payload.operation == operation
    if operation != "list":
        assert plan.payload.name == StreamName("session" if name == "s" else "db", "s")
    if operation == "create":
        assert plan.payload.source == StreamName("session", "orders")
        assert plan.payload.if_not_exists == ("IF NOT EXISTS" in sql)
    assert plan.payload.if_exists == ("IF EXISTS" in sql)
    assert plan.effects.writes_metadata == (operation in {"create", "drop"})
    assert plan.requires_confirmation == (operation == "drop")
    context.database = "changed"
    assert plan.payload.database == ("session" if operation == "list" or name == "s" else "db")
    assert "password" not in str(asdict(plan))
    assert not binder.mock_calls


@pytest.mark.parametrize("sql", [
    "CREATE STREAM s ON TABLE t APPEND_ONLY=FALSE",
    "CREATE STREAM s ON TABLE t",
    "CREATE STREAM TABLE s ON TABLE t APPEND_ONLY=TRUE",
    "CREATE STREAM s ON TABLE t APPEND_ONLY=TRUE garbage",
    "DROP STREAM", "DROP STREAM IF NOT EXISTS db.s", "DROP STREAM db.s CASCADE",
    "DESCRIBE STREAM db..s", "DESC STREAM db.s extra", "SHOW STREAMS db",
    "SHOW STREAM STATUS", "SHOW STREAM BACKLOG", "SHOW STREAM BACKLOG db.s extra",
])
def test_invalid_grammar(sql):
    with pytest.raises(SQLFrontendError):
        parse_statement(sql)


async def test_missing_database_rejected_for_show_and_source():
    for sql in ("SHOW STREAMS", "CREATE STREAM db.s ON TABLE t APPEND_ONLY=TRUE"):
        with pytest.raises(SQLFrontendError, match="database"):
            await SQLPlanner().plan(ast_builders.build(parse_statement(sql)), PlanningContext())


async def test_native_stream_load_and_catalog_names_unchanged():
    for sql in ("SHOW STREAM LOAD", "SELECT * FROM hive.db.t"):
        node = ast_builders.build(parse_statement(sql))
        assert isinstance(node, NativeStatement)
        assert (await SQLPlanner().plan(node, PlanningContext())).engine_sql == sql


def test_runtime_flags_cannot_enable_an_unverified_provider(monkeypatch):
    from app.modules.streams import runtime
    from app.sql_frontend.errors import CapabilityUnsupportedError

    for enabled, message in ((False, "disabled"), (True, "provider is unavailable")):
        monkeypatch.setattr(runtime.settings, "STREAMS_ENABLED", enabled)
        with pytest.raises(CapabilityUnsupportedError, match=message):
            runtime.require_stream_runtime()


@pytest.mark.parametrize("argument", ["'s'", "'db.s'", "'db.default.s'"])
async def test_has_data_uses_same_frozen_namespace(argument):
    sql = f"SELECT NOVA_STREAM_HAS_DATA({argument})"
    plan = await SQLPlanner().plan(
        ast_builders.build(parse_statement(sql)), PlanningContext(database="db"),
    )
    binding, = plan.stream_functions
    assert binding.name == StreamName("db", "s")
    assert sql[binding.start:binding.end] == f"NOVA_STREAM_HAS_DATA({argument})"


@pytest.mark.parametrize("argument", ["col", "'db.s', 'db.t'", "'db.other.s'", "''", ""])
async def test_has_data_rejects_unbindable_arguments(argument):
    sql = f"SELECT NOVA_STREAM_HAS_DATA({argument})"
    with pytest.raises(SQLFrontendError):
        await SQLPlanner().plan(
            ast_builders.build(parse_statement(sql)), PlanningContext(database="db"),
        )


async def test_stream_keywords_remain_usable_as_native_identifiers():
    sql = "SELECT streams, backlog, append_only FROM streams"
    assert (await SQLPlanner().plan(
        ast_builders.build(parse_statement(sql)), PlanningContext(),
    )).engine_sql == sql


async def test_relation_cte_precedes_stream_lookup_and_alias_is_retained():
    from app.sql_frontend.binding.catalog import Binder
    from app.sql_frontend.binding.models import BoundOutputColumn, BoundRelation
    from app.sql_frontend.binding.relations import RelationBinder
    from app.sql_frontend.binding.types import parse_sql_type

    stream = AsyncMock(return_value=("id", BoundRelation((
        BoundOutputColumn("id", parse_sql_type("BIGINT"), False),
    ))))
    engine = AsyncMock()
    binder = RelationBinder(Binder(engine), database="db", stream_schema=stream)
    await binder.bind_relation("WITH s AS (SELECT 1 AS id) SELECT * FROM s")
    stream.assert_not_awaited()
    await binder.bind_relation("SELECT alias.* FROM db.s AS alias")
    stream.assert_awaited_once_with(StreamName("db", "s"))
    assert binder.stream_bindings[0].alias == "alias"
    engine.resolve_table.assert_not_awaited()


async def test_describe_table_named_stream_retains_native_meaning():
    sql = "DESCRIBE STREAM"
    statement = ast_builders.build(parse_statement(sql))
    assert isinstance(statement, NativeStatement)
    assert (await SQLPlanner().plan(statement, PlanningContext())).engine_sql == sql
