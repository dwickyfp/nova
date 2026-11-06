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
async def test_catalog_trajectory_blocks_queries(monkeypatch, question):
    monkeypatch.setattr("app.modules.agents.tools.describe_agent.write_audit_log",
                        AsyncMock(return_value="audit"))
    registry = ToolRegistry()
    query = EvalTool("query_execute")
    registry.register(query)
    registry.register(DescribeAgentTool(registry, name="Business"))
    provider = ScriptedProvider([
        tool_call_frame("bad", name="query_execute", arguments={"sql": "SHOW DATABASES"}),
        tool_call_frame("catalog", name="describe_agent", arguments={"offset": 0}),
        text_frame("Belum ada sumber bisnis terhubung."),
    ], turn_plan={"intent": "agent_catalog", "tools": ["query_execute"],
                  "required_tools": ["query_execute"]})
    context = LoopContext("reader", agent_id="business")
    consent = AsyncMock(return_value=False)
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="Business", max_iterations=5,
    ).run(thread=thread(), user_content=question, context=context, resolve_consent=consent)])
    assert result.finish_reason == "stop", result.error_codes
    assert not query.runs
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


@pytest.mark.asyncio
async def test_the_model_is_shown_the_rows_it_is_asked_to_write_about():
    """A result that only reports its row count makes the model write blind."""
    seen: list[list[dict]] = []

    class Recording(ScriptedProvider):
        async def stream(self, *, messages, **kwargs):
            seen.append([dict(message) for message in messages])
            async for frame in super().stream(messages=messages, **kwargs):
                yield frame

    table = {"columns": ["total_expense"], "rows": [["58851000000.00"]]}
    registry = ToolRegistry()
    registry.register(EvalTool("semantic_query", outcomes=[ToolOutcome(
        ok=True, summary="1 row", table=table,
        data={"semantic_plan": {"metrics": ["total_expense"]}, "sql": "SELECT 1",
              "row_count": 1},
    )]))
    registry.register(DescribeAgentTool(registry, name="Finance"))
    provider = Recording([
        tool_call_frame("q", name="semantic_query", arguments={"sql": "total expense"}),
        text_frame("Total expense is 58851000000.00."),
    ], turn_plan={"intent": "semantic_analytics", "tools": ["semantic_query"],
                  "required_tools": ["semantic_query"]})
    result = TurnResult(frames=[frame async for frame in AssistantLoop(
        provider=provider, registry=registry, system_prompt="Finance", max_iterations=6,
        iterative=True,
    ).run(thread=thread(read_only_grant=True), user_content="Total expense?",
          context=LoopContext("reader", agent_id="finance"),
          resolve_consent=AsyncMock(return_value=False))])
    assert result.finish_reason == "stop", result.error_codes
    tool_message = next(m for m in seen[-1] if "semantic_query" in str(m.get("content"))
                        and "row_count" in str(m.get("content")))
    assert '"rows":[["58851000000.00"]]' in str(tool_message["content"]).replace(" ", "")
