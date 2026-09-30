from dataclasses import dataclass, replace
from unittest.mock import AsyncMock

import pytest

from app.modules.query.repository import QueryResult
from app.modules.query.service import QueryService
from app.sql_frontend.analysis.effects import PlanEffects
from app.sql_frontend.analysis.semantics import StatementSemantics, default_semantics
from app.sql_frontend.ast.builder import AstBuilderRegistry, ast_builders
from app.sql_frontend.ast.statements import Statement
from app.sql_frontend.binding.models import (
    BoundColumn,
    BoundOutputColumn,
    BoundRelation,
    BoundTable,
    TableName,
)
from app.sql_frontend.binding.schema import compare_schema, map_by_name
from app.sql_frontend.binding.types import (
    TypeCompatibility,
    TypeCompatibilityChecker,
    parse_sql_type,
)
from app.sql_frontend.capabilities.starrocks import StarRocksCapabilityProvider
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import BindingError, SemanticError
from app.sql_frontend.execution.executor import ActionHandlerRegistry
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import NovaActionPlan, SourcePayload
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.planning.registry import PlannerRegistry
from app.sql_frontend.rules.registry import RuleRegistry


async def test_new_mutation_registers_semantics_binding_rule_and_typed_handler(monkeypatch):
    calls = []

    class Mutation(Statement):
        pass

    @dataclass(frozen=True)
    class Payload(SourcePayload):
        value: int

    def analyze(statement):
        calls.append("analyze")
        return statement

    def validate(statement, context):
        calls.append("validate")
        return 42

    semantics = default_semantics()
    effects = PlanEffects(reads_data=True, writes_data=True, deletes_rows=True, changes_schema=True)
    semantics.register(
        Mutation, StatementSemantics(lambda _: effects, analyzer=analyze, validator=validate)
    )

    class Planner:
        async def plan(self, logical, context):
            bound = await context.relation_binder.bind_relation("SELECT id AS k FROM t")
            assert [column.name for column in bound.columns] == ["k"]
            return NovaActionPlan(
                "future_mutation",
                Payload(logical.source_key, context.validated[logical.source_key]),
                logical.analysis.effects,
                logical.analysis.requires_confirmation,
            )

    class Rule:
        name = "future_rule"

        def matches(self, plan, context):
            return isinstance(plan, NovaActionPlan) and isinstance(plan.payload, Payload)

        async def apply(self, plan, context):
            calls.append("rule")
            return replace(plan, payload=replace(plan.payload, value=43))

    registry = PlannerRegistry()
    registry.register(Mutation, Planner())
    rules = RuleRegistry()
    rules.register(Rule())
    builders = AstBuilderRegistry()
    builders.register("QueryStatementContext", Mutation)
    handlers = ActionHandlerRegistry()
    handler = AsyncMock(return_value=QueryResult(affected_rows=1))
    handlers.register(Payload, handler)
    monkeypatch.setattr("app.sql_frontend.execution.executor.action_handler_registry", handlers)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    catalog = AsyncMock()
    catalog.resolve_table.side_effect = lambda name: BoundTable(name, "BASE TABLE")
    catalog.get_columns.return_value = (BoundColumn("id", "INT", 1, False),)
    service = QueryService(
        builders=builders,
        planner=SQLPlanner(registry, rules, semantics),
        catalog_provider_factory=lambda *args: catalog,
        capability_resolver=AsyncMock(return_value=StarRocksCapabilityProvider().for_version()),
    )
    refusal = await service.execute_statements("SELECT 1", "alice", "enc", database="db")
    assert refusal[0].needs_confirmation and refusal[0].effects["deletes_rows"]
    assert "validate" not in calls
    handler.assert_not_awaited()
    result = await service.execute_statements(
        "SELECT 1", "alice", "enc", database="db", confirm_destructive=True
    )
    assert result[0].success and result[0].destructive and not result[0].needs_confirmation
    assert result[0].effects["changes_schema"]
    assert handler.await_args.args[0].payload.value == 43
    assert calls.count("validate") == 1 and calls.count("rule") == 1


async def test_warm_native_lifecycle_has_zero_binding_and_capability_network_calls(monkeypatch):
    provider = StarRocksCapabilityProvider()
    reader = AsyncMock(return_value="4.1.4-4a9848e")
    warmed = await provider.detect("target", reader)

    async def resolve():
        return await provider.detect("target", reader)

    catalog = AsyncMock()
    service = QueryService(
        capability_resolver=resolve, catalog_provider_factory=lambda *args: catalog
    )
    service._repo.execute_as_user = AsyncMock(return_value=QueryResult(rows=[[1]]))
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda _: "pw")
    for _ in range(10):
        assert (await service.execute_statements("SELECT 1", "alice", "enc"))[0].success
    reader.assert_awaited_once()
    assert catalog.mock_calls == []
    assert warmed.identity.version.prerelease is None and warmed.identity.build == "4a9848e"


@pytest.mark.parametrize(
    "source,target,result",
    [
        ("MAP<INT,INT>", "MAP<INT,BIGINT>", "SAFE_WIDEN"),
        ("MAP<INT,INT>", "MAP<BIGINT,INT>", "UNKNOWN"),
        ("STRUCT<id INT, label VARCHAR(8)>", "STRUCT<id BIGINT,label VARCHAR(32)>", "SAFE_WIDEN"),
        ("STRUCT<id INT>", "STRUCT<other INT>", "INCOMPATIBLE"),
        ("DATETIME", "VARCHAR(20)", "INCOMPATIBLE"),
        ("BOOLEAN", "BOOLEAN", "EXACT"),
    ],
)
def test_struct_map_and_non_numeric_compatibility(source, target, result):
    assert TypeCompatibilityChecker.compare(
        parse_sql_type(source), parse_sql_type(target)
    ) == getattr(TypeCompatibility, result)


@pytest.mark.parametrize(
    "flag", ["generated", "key_column", "partition_column", "auto_increment", "hidden"]
)
def test_schema_delta_marks_protected_or_unknown_changes_unsafe(flag):
    source = BoundRelation((BoundOutputColumn("id", parse_sql_type("BIGINT")),))
    kwargs = {
        "auto_increment": False,
        "key_column": False,
        "partition_column": False,
        "hidden": False,
    }
    if flag == "generated":
        kwargs["generated_expression"] = "x+1"
    else:
        kwargs[flag] = True
    table = BoundTable(
        TableName("t", "db"),
        "BASE TABLE",
        (BoundColumn("id", "INT", 1, False, **kwargs),),
        key_type="PRIMARY",
        partition_columns=(),
    )
    delta = compare_schema(source, table)
    assert not delta.safe_candidate
    assert ("generated_column" if flag == "generated" else flag) in delta.unsafe_reasons


def test_name_mapping_extra_columns_type_mismatch_and_ambiguity():
    target = BoundRelation((BoundOutputColumn("id", parse_sql_type("INT")),))
    with pytest.raises(BindingError, match="Extra"):
        map_by_name(BoundRelation(target.columns + (BoundOutputColumn("phone"),)), target)
    with pytest.raises(BindingError, match="types"):
        map_by_name(BoundRelation((BoundOutputColumn("id", parse_sql_type("DATETIME")),)), target)
    with pytest.raises(BindingError, match="Duplicate"):
        map_by_name(
            BoundRelation((target.columns[0], replace(target.columns[0], name="ID"))), target
        )


async def test_rules_cannot_add_undeclared_effects_and_declared_mutation_preflights():
    class Rule:
        name = "custom_mutation"
        effect_bound = PlanEffects(updates_rows=True, writes_data=True)

        def matches(self, plan, context):
            return True

        async def apply(self, plan, context):
            return replace(
                plan, effects=plan.effects | self.effect_bound, requires_confirmation=True
            )

    rules = RuleRegistry()
    rules.register(Rule())
    planner = SQLPlanner(rules=rules)
    statement = ast_builders.build(parse_statement("SELECT 1"))
    assert planner.preflight(statement).requires_confirmation
    assert (await planner.plan(statement, PlanningContext())).requires_confirmation
    Rule.effect_bound = PlanEffects()

    async def bad_apply(self, plan, context):
        return replace(plan, effects=plan.effects | PlanEffects(deletes_rows=True))

    Rule.apply = bad_apply
    with pytest.raises(SemanticError, match="declared"):
        await planner.plan(statement, PlanningContext())
