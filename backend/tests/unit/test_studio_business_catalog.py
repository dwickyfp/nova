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


def smart_catalog(monkeypatch, bindings):
    """Patch discovery so each specialist resolves the given models (or raises)."""
    from app.modules.agents.auto_planner import Candidate
    from app.modules.agents.capabilities import CapabilityManifest

    candidates = [
        Candidate(agent_id, agent_id.title(), CapabilityManifest(owns=[agent_id]), (),
                  owner_name="owner", view_ids=tuple(f"{agent_id}-{i}" for i in range(bound)))
        for agent_id, (_, bound) in bindings.items()
    ]

    async def load(candidate, context):
        models, bound = bindings[candidate.agent_id]
        if isinstance(models, Exception):
            raise models
        return models, bound

    discover = AsyncMock(return_value=candidates)
    monkeypatch.setattr("app.modules.agents.auto_planner.authorized_candidates", discover)
    monkeypatch.setattr(describe_agent, "load_specialist_models", load)
    return discover


def smart_context():
    return LoopContext("reader", agent_id="smart", collaboration_root=True, user={})


@pytest.mark.asyncio
async def test_smart_catalog_combines_specialist_views_without_spawning(
    monkeypatch, models, audit
):
    marketing = [{**models[0], "semantic_model_id": "marketing", "name": "Marketing"}]
    smart_catalog(monkeypatch, {"sales": (models, 1), "marketing": (marketing, 1)})
    registry = ToolRegistry()
    spawn, query = EvalTool("spawn_agent"), EvalTool("query_execute")
    registry.register(spawn)
    registry.register(query)
    result = await DescribeAgentTool(registry, name="Smart").run(invocation(), smart_context())
    assert result.ok and not spawn.runs and not query.runs
    views = result.data["semantic_views"]
    assert [(view["name"], [agent["name"] for agent in view["agents"]]) for view in views] == [
        ("Marketing", ["Marketing"]), ("Sales", ["Sales"]),
    ]
    assert views[0]["metrics"] and views[0]["dimensions"]
    assert result.data["total"] == 2
    assert [item["view_count"] for item in result.data["specialists"]] == [1, 1]
    assert result.summary == "Read 2 Semantic Views across Sales and Marketing."


@pytest.mark.asyncio
async def test_smart_catalog_lists_a_shared_view_once_with_both_agents(
    monkeypatch, models, audit
):
    smart_catalog(monkeypatch, {"sales": (models, 1), "finance": (models, 1)})
    result = await DescribeAgentTool(ToolRegistry()).run(invocation(), smart_context())
    assert result.data["total"] == 1
    assert [agent["agent_id"] for agent in result.data["semantic_views"][0]["agents"]] == [
        "sales", "finance",
    ]


@pytest.mark.asyncio
async def test_smart_catalog_keeps_differently_pinned_versions_apart(monkeypatch, models, audit):
    pinned = [{**models[0], "version": 1}]
    smart_catalog(monkeypatch, {"sales": (models, 1), "finance": (pinned, 1)})
    result = await DescribeAgentTool(ToolRegistry()).run(invocation(), smart_context())
    assert [(view["version"], view["agents"][0]["agent_id"])
            for view in result.data["semantic_views"]] == [("1", "finance"), ("2", "sales")]


@pytest.mark.asyncio
async def test_smart_catalog_isolates_an_unavailable_specialist(monkeypatch, models, audit):
    from fastapi import HTTPException

    drift = HTTPException(409, "private pin detail")
    smart_catalog(monkeypatch, {"sales": (models, 2), "finance": (drift, 3)})
    result = await DescribeAgentTool(ToolRegistry()).run(invocation(), smart_context())
    assert result.ok and result.data["total"] == 1
    assert [item["catalog_status"] for item in result.data["specialists"]] == [
        "ok", "unavailable",
    ]
    assert result.data["unavailable_binding_count"] == 4
    assert "private" not in json.dumps(result.data)


@pytest.mark.asyncio
async def test_smart_catalog_outage_is_not_reported_as_empty(monkeypatch, audit):
    smart_catalog(monkeypatch, {"sales": (RuntimeError("private connection"), 1)})
    result = await DescribeAgentTool(ToolRegistry()).run(invocation(), smart_context())
    assert not result.ok and result.error_class == "CATALOG_UNAVAILABLE"
    assert "private" not in result.error


@pytest.mark.asyncio
async def test_smart_catalog_paginates_views_and_reads_metadata_once(
    monkeypatch, models, audit
):
    sales = [{**models[0], "semantic_model_id": f"s{i}", "name": f"view-{i}"} for i in range(3)]
    finance = [{**models[0], "semantic_model_id": f"f{i}", "name": f"view-{i + 3}"}
               for i in range(3)]
    discover = smart_catalog(monkeypatch, {"sales": (sales, 3), "finance": (finance, 3)})
    tool, context = DescribeAgentTool(ToolRegistry()), smart_context()
    first = await tool.run(invocation(), context)
    second = await tool.run(invocation(offset=4), context)
    assert [view["name"] for view in first.data["semantic_views"]] == [
        "view-0", "view-1", "view-2", "view-3",
    ]
    assert first.data["next_offset"] == 4 and first.data["total"] == 6
    assert len(second.data["semantic_views"]) == 2 and second.data["next_offset"] is None
    discover.assert_awaited_once()


@pytest.mark.asyncio
async def test_specialist_views_resolve_through_its_release_pin(monkeypatch):
    from app.modules.agents import releases
    from app.modules.agents.auto_planner import Candidate
    from app.modules.agents.capabilities import CapabilityManifest
    from app.modules.agents.semantic import access

    manifest = {"dependencies": {
        "configuration": {"semantic_view_ids": ["pinned-view"]},
        "semantic_views": [{"view_id": "pinned-view", "version": 1, "fingerprint": "f"}],
    }}
    monkeypatch.setattr(releases, "load_runtime_manifest", AsyncMock(return_value=manifest))
    seen = {}

    async def load(context):
        seen.update(vars(context))
        return []

    monkeypatch.setattr(access, "load_authorized_models", load)
    candidate = Candidate("sales", "Sales", CapabilityManifest(), (), owner_name="owner",
                          release_manifest_id="release", view_ids=("draft-view",))
    caller = LoopContext("reader", agent_id="smart", role="ANALYST",
                         user={"username": "reader"})
    assert await access.load_specialist_models(candidate, caller) == ([], 1)
    assert seen["agent_id"] == "sales"
    assert seen["semantic_view_ids"] == ["pinned-view"]
    assert seen["release_manifest"] is manifest
    assert seen["user"] == {"username": "reader"} and seen["role"] == "ANALYST"


def test_smart_scope_points_at_the_combined_catalog():
    scope = DescribeAgentTool(ToolRegistry(), name="Smart").planning_scope(smart_context())
    assert scope["catalog_scope"] == "accessible_specialists"
    assert "semantic_views" not in scope


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


def test_scope_names_dimensions_shared_across_facts():
    from tests.benchmark.studio_accuracy.model import bench_model

    models = [{"semantic_model_id": "nova_bench", "name": "nova_bench",
               "_scoped_ir": bench_model()}]
    scope = DescribeAgentTool(ToolRegistry(), name="Bench").planning_scope(
        LoopContext("reader", authorized_semantic_models=models)
    )
    assert scope["semantic_views"][0]["shared_dimensions"] == [
        ["sales_channel", "marketing_channel"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("smart", [False, True])
async def test_only_smart_is_planned_with_specialist_routing(smart):
    class Provider:
        async def complete(self, **kwargs):
            self.messages = kwargs["messages"]
            return {"content": json.dumps({"intent": "agent_catalog", "tools": [],
                                           "required_tools": []})}

    registry = ToolRegistry()
    tool = DescribeAgentTool(registry, name="Smart" if smart else "Sales")
    registry.register(tool)
    context = LoopContext("reader", agent_id="agent", collaboration_root=smart)
    provider = Provider()
    await plan_turn(provider_client=provider, provider=object(), registry=registry,
                    user_content="Berapa total expense 2025?",
                    agent_scope=tool.planning_scope(context))
    instructions = provider.messages[0]["content"]
    assert ("discover_agents as the only required tool" in instructions) is smart
