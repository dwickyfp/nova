"""Studio inventory must stay on the metadata path in the production loop."""

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools.describe_agent import DescribeAgentTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "data apa saja yang kamu punya", "What data can you help me with?",
    "Sumber lain yang tersedia apa?", "Kamu bisa bantu apa?",
])
@pytest.mark.parametrize("smart", [False, True])
async def test_catalog_trajectory_blocks_queries_and_delegation(monkeypatch, question, smart):
    monkeypatch.setattr("app.modules.agents.tools.describe_agent.write_audit_log",
                        AsyncMock(return_value="audit"))
    monkeypatch.setattr("app.modules.agents.auto_planner.authorized_candidates",
                        AsyncMock(return_value=[]))
    registry = ToolRegistry()
    query = EvalTool("query_execute")
    spawn = EvalTool("spawn_agent")
    registry.register(query)
    registry.register(spawn)
    registry.register(DescribeAgentTool(registry, name="Business"))
    provider = ScriptedProvider([
        tool_call_frame("bad", name="query_execute", arguments={"sql": "SHOW DATABASES"}),
        tool_call_frame("catalog", name="describe_agent", arguments={"offset": 0}),
        text_frame("Belum ada sumber bisnis terhubung."),
    ], turn_plan={"intent": "agent_catalog", "tools": ["query_execute"],
                  "required_tools": ["query_execute"]})
    context = LoopContext("reader", agent_id="business", collaboration_root=smart,
                          collaboration_tools=("spawn_agent",) if smart else ())
    consent = AsyncMock(return_value=False)
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="Business", max_iterations=5,
    ).run(thread=thread(), user_content=question, context=context, resolve_consent=consent)])
    assert result.finish_reason == "stop", result.error_codes
    assert not query.runs and not spawn.runs
    consent.assert_not_awaited()
    assert context.selected_tools == ["describe_agent"]
    assert context.agent_scope["name"] == "Business"
    assert "encrypted_password" not in "".join(result.frames)


@pytest.mark.asyncio
async def test_catalog_cannot_be_skipped_by_unsupported_answer(monkeypatch):
    registry = ToolRegistry()
    registry.register(DescribeAgentTool(registry))
    provider = ScriptedProvider([text_frame("I have payroll and every database.")],
                               turn_plan={"intent": "agent_catalog", "tools": [],
                                          "required_tools": []})
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, max_iterations=3,
    ).run(thread=thread(), user_content="Data apa saja?",
          context=LoopContext("reader", agent_id="business"),
          resolve_consent=AsyncMock(return_value=False))])
    assert result.finish_reason != "stop"
    assert not any(frame.startswith("event: text_delta") for frame in result.frames)


@pytest.mark.asyncio
async def test_smart_catalog_trajectory_aggregates_two_specialists(monkeypatch):
    from pathlib import Path

    from app.modules.agents.auto_planner import Candidate
    from app.modules.agents.capabilities import CapabilityManifest
    from app.modules.agents.semantic.ir import SemanticModelIR
    from app.modules.agents.semantic.ossie import parse_ossie
    from app.modules.agents.tools import describe_agent

    source = Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    model = SemanticModelIR.from_ossie(parse_ossie(source).as_dict())
    owned = {
        "sales": {"semantic_model_id": "sales", "name": "Sales", "version": 1},
        "marketing": {"semantic_model_id": "marketing", "name": "Marketing", "version": 1},
    }

    async def load(candidate, context):
        return [{**owned[candidate.agent_id], "_scoped_ir": model}], 1

    monkeypatch.setattr(describe_agent, "write_audit_log", AsyncMock(return_value="audit"))
    monkeypatch.setattr(describe_agent, "load_specialist_models", load)
    monkeypatch.setattr("app.modules.agents.auto_planner.authorized_candidates", AsyncMock(
        return_value=[Candidate(agent_id, agent_id.title(), CapabilityManifest(), (),
                                owner_name="owner", view_ids=(agent_id,))
                      for agent_id in owned],
    ))
    registry = ToolRegistry()
    query, spawn = EvalTool("query_execute"), EvalTool("spawn_agent")
    registry.register(query)
    registry.register(spawn)
    registry.register(DescribeAgentTool(registry, name="Smart"))
    provider = ScriptedProvider([
        tool_call_frame("delegate", name="spawn_agent", arguments={
            "agent": "sales", "task_name": "catalog", "objective": "List your data"}),
        tool_call_frame("catalog", name="describe_agent", arguments={"offset": 0}),
        text_frame("Saya punya Semantic View Sales (agent Sales) dan Marketing (agent Marketing)."),
    ], turn_plan={"intent": "agent_catalog", "tools": [], "required_tools": []})
    context = LoopContext("reader", agent_id="__smart__", collaboration_root=True,
                          collaboration_tools=("spawn_agent",))
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="Smart", max_iterations=5,
    ).run(thread=thread(), user_content="data apa yang kamu punya?", context=context,
          resolve_consent=AsyncMock(return_value=False))])
    assert result.finish_reason == "stop", result.error_codes
    assert not query.runs and not spawn.runs
    assert context.selected_tools == ["describe_agent"]
    assert context.agent_scope["catalog_scope"] == "accessible_specialists"
    assert [(view["name"], view["agents"][0]["name"])
            for view in context.collaboration_catalog["views"]] == [
        ("Marketing", "Marketing"), ("Sales", "Sales"),
    ]


@pytest.mark.asyncio
async def test_an_analyst_turn_keeps_the_analysis_tools_its_owner_enabled(monkeypatch):
    """The planner picked only the query; the forecast the owner enabled is still callable."""
    table = {"columns": ["month", "total_expense"], "rows": [["2026-09", "4805"]]}
    forecast = {"columns": ["timestamp", "prediction"], "rows": [["2026-10-31", 4805.0]]}
    registry = ToolRegistry()
    query = EvalTool("semantic_query", outcomes=[ToolOutcome(
        ok=True, summary="1 row", table=table,
        data={"semantic_plan": {"metrics": ["total_expense"]}, "sql": "SELECT 1"},
    )])
    ml = EvalTool("ml_execute", outcomes=[ToolOutcome(
        ok=True, summary="forecast completed", table=forecast, trace_detail={"run_id": "run-1"},
    )])
    registry.register(query)
    registry.register(ml)
    registry.register(DescribeAgentTool(registry, name="Finance"))
    provider = ScriptedProvider([
        tool_call_frame("q", name="semantic_query", arguments={"sql": "monthly expense"}),
        tool_call_frame("f", name="ml_execute", arguments={"sql": "forecast"}),
        text_frame("October is forecast at 4805."),
    ], turn_plan={"intent": "semantic_analytics", "tools": ["semantic_query"],
                  "required_tools": ["semantic_query"]})
    context = LoopContext("reader", agent_id="finance")
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="Finance", max_iterations=8,
        iterative=True,
    ).run(thread=thread(read_only_grant=True), user_content="Forecast expense next month",
          context=context, resolve_consent=AsyncMock(return_value=False))])
    assert result.finish_reason == "stop", result.error_codes
    assert len(query.runs) == 1 and len(ml.runs) == 1
    assert "ml_execute" in context.selected_tools
