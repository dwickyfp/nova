"""Business catalog projections and planner isolation from Nove."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.tools import describe_agent
from app.modules.agents.tools.describe_agent import DescribeAgentTool, semantic_catalog
from app.modules.assistant.planning import plan_turn, validate_turn_plan
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from tests.eval.harness import EvalTool


@pytest.fixture
def models():
    source = Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    definition = parse_ossie(source).as_dict()
    model = SemanticModelIR.from_ossie(definition)
    return [{"semantic_model_id": "sales", "name": "Sales", "version": 2, "_scoped_ir": model}]


@pytest.fixture
def audit(monkeypatch):
    audit = AsyncMock(return_value="audit-id")
    monkeypatch.setattr(describe_agent, "write_audit_log", audit)
    return audit


def invocation(**arguments):
    return ToolInvocation(tool_call_id="catalog", tool_name="describe_agent", arguments=arguments)


def test_catalog_excludes_physical_sources_and_credentials(models):
    models[0]["name"] = "sk-" + "a" * 32
    catalog = semantic_catalog(models)
    rendered = json.dumps(catalog)
    assert "sk-" not in rendered
    assert "expression" not in rendered
    for dataset in models[0]["_scoped_ir"].datasets:
        assert dataset.source not in rendered
    assert catalog[0]["metrics"]
    assert catalog[0]["dimensions"]


@pytest.mark.asyncio
async def test_catalog_reads_only_authorized_bound_metadata(models, audit):
    registry = ToolRegistry()
    registry.register(EvalTool("semantic_query"))
    registry.default_skills = ("sales-playbook",)
    tool = DescribeAgentTool(registry, name="Sales")
    context = LoopContext("reader", agent_id="shared-sales", semantic_view_ids=["sales"],
                          authorized_semantic_models=models)
    result = await tool.run(invocation(), context)
    assert result.ok
    assert result.data["semantic_views"][0]["name"] == "Sales"
    assert result.data["skills"] == ["sales-playbook"]
    assert result.metadata["evidence_kind"] == "agent_catalog"
    assert not registry.get("semantic_query").runs
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalog_empty_is_not_global_discovery(monkeypatch, audit):
    from app.modules.intelligence.semantic_views import semantic_view_service

    global_list = AsyncMock(side_effect=AssertionError("global discovery"))
    monkeypatch.setattr(semantic_view_service, "list_active_for_agent", global_list)
    result = await DescribeAgentTool(ToolRegistry()).run(invocation(), LoopContext(
        "reader", agent_id="empty", semantic_view_ids=[],
        user={"username": "reader", "encrypted_password": "test"},
    ))
    assert result.ok and result.data["semantic_views"] == []
    global_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_binding_is_distinct_from_unconfigured(audit):
    result = await DescribeAgentTool(ToolRegistry()).run(invocation(), LoopContext(
        "reader", agent_id="sales", semantic_view_ids=["restricted-view"],
        authorized_semantic_models=[],
    ))
    assert result.ok and result.data["semantic_views"] == []
    assert result.data["unavailable_binding_count"] == 1
    assert "restricted-view" not in json.dumps(result.data)


@pytest.mark.asyncio
async def test_catalog_unavailable_does_not_claim_empty_or_leak_error(monkeypatch, audit):
    monkeypatch.setattr(describe_agent, "load_authorized_models", AsyncMock(
        side_effect=RuntimeError("private connection information"),
    ))
    result = await DescribeAgentTool(ToolRegistry()).run(
        invocation(), LoopContext("reader", agent_id="sales"),
    )
    assert not result.ok and result.error_class == "CATALOG_UNAVAILABLE"
    assert "private" not in result.error
    assert audit.await_args.kwargs["status"] == "FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"offset": -1}, {"offset": True}, {"offset": "0"}, {"agent_id": "another-agent"},
])
async def test_catalog_rejects_invalid_pages_and_agent_override(monkeypatch, arguments):
    load = AsyncMock()
    monkeypatch.setattr(describe_agent, "load_authorized_models", load)
    result = await DescribeAgentTool(ToolRegistry()).run(
        invocation(**arguments), LoopContext("reader", agent_id="sales"),
    )
    assert not result.ok
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_paginates_without_claiming_complete(models, audit):
    records = [{**models[0], "name": f"view-{i}"} for i in range(6)]
    context = LoopContext("reader", agent_id="sales", authorized_semantic_models=records)
    tool = DescribeAgentTool(ToolRegistry())
    first = await tool.run(invocation(), context)
    second = await tool.run(invocation(offset=4), context)
    assert first.data["next_offset"] == 4
    assert len(first.data["semantic_views"]) == 4
    assert len(second.data["semantic_views"]) == 2
    assert second.data["next_offset"] is None


@pytest.mark.asyncio
async def test_smart_catalog_does_not_spawn_or_query(monkeypatch, audit):
    from app.modules.agents.auto_planner import Candidate
    from app.modules.agents.capabilities import CapabilityManifest

    candidate = Candidate("finance", "Finance", CapabilityManifest(owns=["revenue"]),
                          ({"name": "revenue"},), semantic_views=({"name": "Finance view"},))
    discover = AsyncMock(return_value=[candidate])
    monkeypatch.setattr("app.modules.agents.auto_planner.authorized_candidates", discover)
    context = LoopContext("reader", agent_id="smart", collaboration_root=True, user={})
    result = await DescribeAgentTool(ToolRegistry(), name="Smart").run(invocation(), context)
    assert result.ok
    assert result.data["specialists"][0]["metrics"] == ["revenue"]
    assert result.data["specialists"][0]["semantic_views"] == [{"name": "Finance view"}]


def test_studio_does_not_substitute_raw_sql_for_semantic_tool():
    plan = validate_turn_plan({"intent": "semantic_analytics", "tools": [],
                               "required_tools": ["semantic_query"]},
                              {"describe_agent", "query_execute"})
    assert plan.route.required_capabilities == ("semantic_query",)
    assert not plan.selected_tools
    nove = validate_turn_plan({"intent": "semantic_analytics", "tools": [],
                               "required_tools": ["semantic_query"]}, {"query_execute"})
    assert nove.route.required_capabilities == ("query_execute",)


def test_catalog_plan_cannot_execute_data_or_skills():
    plan = validate_turn_plan({"intent": "agent_catalog", "tools": ["query_execute"],
                               "required_tools": ["query_execute"], "skills": ["sql"]},
                              {"describe_agent", "query_execute"}, {"sql"})
    assert plan.selected_tools == plan.route.required_capabilities == ("describe_agent",)
    assert not plan.selected_skills
    assert not plan.route.needs_data


@pytest.mark.asyncio
async def test_real_planning_payload_contains_scope(models):
    class Provider:
        async def complete(self, **kwargs):
            self.messages = kwargs["messages"]
            return {"content": json.dumps({"intent": "agent_catalog", "tools": [],
                                           "required_tools": []})}

    registry = ToolRegistry()
    tool = DescribeAgentTool(registry, name="Sales")
    registry.register(tool)
    scope = tool.planning_scope(LoopContext("reader", agent_id="sales",
                                           authorized_semantic_models=models))
    provider = Provider()
    plan = await plan_turn(provider_client=provider, provider=object(), registry=registry,
                           user_content="data apa saja yang kamu punya", agent_scope=scope)
    assert plan.selected_tools == ("describe_agent",)
    payload = json.loads(provider.messages[1]["content"])
    assert payload["agent_scope"]["semantic_views"][0]["name"] == "Sales"


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "SHOW DATABASES", "SHOW TABLES", "SELECT * FROM payroll", "USE ROLE ACCOUNTADMIN",
])
async def test_studio_raw_sql_denied_before_engine_even_for_admin(monkeypatch, sql):
    from app.modules.assistant.tools.query_execute import QueryExecuteTool
    from app.modules.query.service import query_service

    engine = AsyncMock()
    monkeypatch.setattr(query_service, "execute_statements", engine)
    tool = QueryExecuteTool()
    tool._audit = AsyncMock()
    context = LoopContext("admin", agent_id="sales", role="ACCOUNTADMIN", user={
        "username": "admin", "encrypted_password": "test-only",
    })
    result = await tool.run(ToolInvocation(tool_call_id="sql", tool_name="query_execute",
                                           arguments={"sql": sql}), context)
    assert not result.ok and result.error_class == "POLICY_VIOLATION"
    engine.assert_not_awaited()
    assert tool._audit.await_args.kwargs["status"] == "DENIED"


@pytest.mark.asyncio
async def test_jev_cannot_add_execution_to_catalog_plan():
    from app.modules.assistant.intelligence import SkillDefinition
    from app.modules.assistant.planning import refine_turn_plan

    registry = ToolRegistry()
    registry.register(DescribeAgentTool(registry))
    registry.register(EvalTool("load_skill"))
    registry.discoverable_skills = ("sql",)
    registry.skill_definitions["sql"] = SkillDefinition(
        "sql", "SQL tasks", (), "SQL", "platform_skill",
    )
    decision = AsyncMock()
    decision.relevance.return_value = {"tool:describe_agent": 0, "skill:sql": 2}
    plan = validate_turn_plan({"intent": "agent_catalog", "tools": [], "required_tools": []},
                              set(registry.names()))
    refined = await refine_turn_plan(plan, registry, "Data apa saja?", decision)
    assert refined.selected_tools == ("describe_agent",)
    assert refined.route.required_capabilities == ("describe_agent",)
    assert not refined.selected_skills
