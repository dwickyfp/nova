"""Studio inventory must stay on the metadata path in the production loop."""

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools.describe_agent import DescribeAgentTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
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
