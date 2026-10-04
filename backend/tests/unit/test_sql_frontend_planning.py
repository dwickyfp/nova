from dataclasses import asdict, dataclass, replace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ForbiddenSQLError
from app.sql_frontend.analysis.analyzer import analyze
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.analysis.semantics import StatementSemantics, default_semantics
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.ast.statements import Statement
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import BoundColumn, BoundTable, TableName
from app.sql_frontend.capabilities.starrocks import EngineCapabilities
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import (
    Atomicity,
    CompositePlan,
    EngineSqlPlan,
    NovaActionPlan,
)
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.planning.registry import PlannerRegistry
from app.sql_frontend.rules.registry import RuleRegistry


def statement(sql):
    return ast_builders.build(parse_statement(sql))


@pytest.mark.parametrize(
    "sql,flags,confirmation",
    [
        ("SELECT 1", {"reads_data"}, False),
        ("INSERT INTO t SELECT 1", {"reads_data", "writes_data"}, False),
        ("UPDATE t SET x=1 WHERE x=2", {"reads_data", "writes_data", "updates_rows"}, True),
        ("DELETE FROM t WHERE x=1", {"reads_data", "writes_data", "deletes_rows"}, True),
        ("CREATE TABLE t AS SELECT 1", {"reads_data", "writes_data", "changes_schema"}, False),
        ("DROP TABLE t", {"changes_schema", "drops_objects"}, True),
        ("ALTER TABLE t DROP COLUMN x", {"changes_schema", "drops_objects"}, True),
        ("GRANT SELECT ON db.t TO ROLE analyst", {"changes_security"}, False),
        ("SELECT * FROM @s.a.csv", {"reads_data", "external_io"}, False),
        ("EXPLAIN INSERT INTO t SELECT 1", {"reads_data"}, False),
        ("CREATE TASK t AS INSERT INTO sink SELECT 1", {"writes_metadata"}, False),
    ],
)
def test_effects_and_confirmation(sql, flags, confirmation):
    analysis = analyze(statement(sql))
    assert {key for key, value in asdict(analysis.effects).items() if value} == flags
    assert analysis.requires_confirmation is confirmation


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "-- heading\nSELECT 'COPY ML_PREDICT @s; UPDATE' AS txt",
        "WITH c AS (SELECT 1 x) SELECT x FROM c",
        "INSERT INTO t SELECT 1",
        "UPDATE t SET x=2 WHERE x=1",
        "DELETE FROM t WHERE x=1",
        "CREATE TABLE t AS SELECT 1",
        "SHOW TABLES",
        "EXPLAIN SELECT 1",
        "SELECT @x",
    ],
)
async def test_native_is_unchanged_and_never_binds(sql):
    binder = AsyncMock()
    plan = await SQLPlanner().plan(
        statement(sql),
        PlanningContext(binder=binder, confirm_destructive=True, capabilities=EngineCapabilities()),
    )
    assert isinstance(plan, EngineSqlPlan)
    assert plan.engine_sql == sql
    assert binder.mock_calls == []


async def test_stage_rule_adds_safe_per_reference_descriptors():
    plan = await SQLPlanner().plan(
        statement("SELECT * FROM @s.a.csv JOIN @s.b.csv USING(id)"), PlanningContext()
    )
    assert plan.stage_aware
    assert len(plan.stage_bindings) == 2
    assert plan.stage_bindings[0].start != plan.stage_bindings[1].start
    assert "secret" not in str(asdict(plan))


async def test_task_password_and_model_are_actions_and_planning_is_pure(monkeypatch):
    import app.modules.query.service as module

    sink = AsyncMock(side_effect=AssertionError("planning mutated state"))
    monkeypatch.setattr(module, "write_audit_log", sink)
    for sql in [
        "CREATE TASK t AS INSERT INTO sink SELECT 1",
        "ALTER USER 'a' REQUIRE PASSWORD CHANGE",
        "CREATE ML_MODEL m TYPE=REGRESSION TARGET='y' INPUT=(SELECT x,y FROM t)",
    ]:
        plan = await SQLPlanner().plan(statement(sql), PlanningContext())
        assert isinstance(plan, NovaActionPlan)
    sink.assert_not_called()


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE ML_MODEL m TYPE=REGRESSION TARGET='y' CONFIG='{}' AS SELECT x,y FROM t",
        "COPY INTO sink FROM @s.a.csv FILE_FORMAT = (TYPE='CSV')",
    ],
)
async def test_unsupported_existing_options_are_rejected(sql):
    with pytest.raises(ValueError):
        await SQLPlanner().plan(statement(sql), PlanningContext())


async def test_semantic_confirmation_is_enforced_independently():
    from app.sql_frontend.context import ExecutionContext
    from app.sql_frontend.execution.executor import SQLExecutor

    plan = await SQLPlanner().plan(statement("UPDATE t SET x=1 WHERE x=2"), PlanningContext())
    assert plan.requires_confirmation
    engine = AsyncMock()
    with pytest.raises(ForbiddenSQLError):
        await SQLExecutor(engine).execute(plan, ExecutionContext("alice"))
    engine.assert_not_awaited()


async def test_rules_run_once_in_order_and_cannot_reduce_effects():
    seen = []

    class Rule:
        def __init__(self, name, effects):
            self.name, self.effects = name, effects
            self.effect_bound = effects

        def matches(self, plan, context):
            return True

        async def apply(self, plan, context):
            seen.append(self.name)
            return replace(plan, effects=self.effects)

    rules = RuleRegistry()
    rules.register(Rule("first", PlanEffects(reads_data=True, external_io=True)))
    rules.register(Rule("second", PlanEffects(reads_data=True, external_io=True)))
    await SQLPlanner(rules=rules).plan(statement("SELECT 1"), PlanningContext())
    assert seen == ["first", "second"]
    with pytest.raises(ValueError):
        rules.register(Rule("first", PlanEffects()))
    bad = RuleRegistry()
    bad.register(Rule("bad", PlanEffects()))
    with pytest.raises(SemanticError):
        await SQLPlanner(rules=bad).plan(statement("SELECT 1"), PlanningContext())


@dataclass(frozen=True)
class DummyStatement(Statement):
    pass


async def test_extension_requests_binding_and_capability_lowering_without_query_service():
    name = TableName("source", "db")
    catalog = AsyncMock()
    catalog.resolve_table.return_value = BoundTable(name, "BASE TABLE")
    catalog.get_columns.return_value = (BoundColumn("id", "INT", 1, False),)

    class DummyPlanner:
        async def plan(self, logical, context):
            column = await context.binder.resolve_column(name, "id")
            assert column.nullable is False
            sql = "SELECT 2" if context.capabilities.native_merge else "SELECT 1"
            return CompositePlan(
                (
                    EngineSqlPlan(sql, sql, logical.analysis.effects),
                    EngineSqlPlan("SELECT 3", "SELECT 3", PlanEffects(reads_data=True)),
                ),
                Atomicity.BEST_EFFORT,
            )

    registry = PlannerRegistry()
    registry.register(DummyStatement, DummyPlanner())
    with pytest.raises(ValueError):
        registry.register(DummyStatement, DummyPlanner())
    dummy = DummyStatement(parse_statement("SELECT 1"))
    semantics = default_semantics()
    semantics.register(DummyStatement, StatementSemantics(lambda _: PlanEffects(reads_data=True)))
    planner = SQLPlanner(registry=registry, semantics=semantics)
    context = PlanningContext(binder=Binder(catalog), capabilities=EngineCapabilities())
    plan = await planner.plan(dummy, context)
    assert plan.steps[0].engine_sql == "SELECT 1"
    context.capabilities = EngineCapabilities(version="future-test", native_merge=True)
    assert (await planner.plan(dummy, context)).steps[0].engine_sql == "SELECT 2"
    catalog.resolve_table.assert_awaited_once()
    catalog.get_columns.assert_awaited_once()
    with pytest.raises(SemanticError):
        await SQLPlanner().plan(dummy, context)


def test_engine_plans_cannot_serialize_credentials():
    sql = "SELECT * FROM FILES('aws.s3.secret_key'='value')"
    with pytest.raises(ValueError, match="credentials"):
        EngineSqlPlan(sql, sql, PlanEffects())
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO db.t (a,b) VALUES(1,2)",
        "INSERT INTO db.t WITH LABEL l (a,b) VALUES(1,2)",
        "INSERT INTO db.t (a,b) WITH LABEL l VALUES(1,2)",
    ],
)
def test_insert_column_lists_preserve_transaction_intent(sql):
    from app.sql_frontend.planning.transactions import transaction_intent

    statement = ast_builders.build(parse_statement(sql))
    intent = transaction_intent(statement, PlanningContext(database="db"))
    assert intent.target == ("default_catalog", "db", "t")
    assert intent.columns == ("a", "b")
