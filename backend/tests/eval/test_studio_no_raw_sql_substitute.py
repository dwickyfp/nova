"""A Studio agent never swaps a missing governed tool for raw SQL (scope review 2026-09-26)."""

from __future__ import annotations

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.eval.harness import EvalTool

PLAN = {
    "intent": "semantic_analytics",
    "tools": ["query_execute"],
    "required_tools": ["semantic_query"],
    "ml_task": None,
}


async def run(*, studio: bool):
    raw = EvalTool("query_execute", table={"columns": ["x"], "rows": [[1]]})
    registry = ToolRegistry()
    registry.register(raw)
    provider = ScriptedProvider(
        script=[tool_call_frame("q1", sql="SELECT SUM(amount) FROM payroll"), text_frame("1")],
        turn_plan=PLAN,
    )
    context = LoopContext(user_name="alice")
    if studio:
        context.agent_scope = {"agent": "Sales"}
    thread = AssistantThread(thread_id="scope", user_name="alice", title="Eval")
    thread.consent.always_allow_read_only = True
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread, user_content="Total payroll?", context=context,
            resolve_consent=allow,
        )
    ]
    return "".join(frames).replace(" ", ""), raw


async def test_studio_reports_the_missing_governed_tool():
    joined, raw = await run(studio=True)
    assert '"finish_reason":"required_capability_unavailable"' in joined
    assert raw.runs == []


async def test_nove_may_still_use_read_only_sql_for_the_same_plan():
    joined, raw = await run(studio=False)
    assert len(raw.runs) == 1
    assert '"finish_reason":"required_capability_unavailable"' not in joined
