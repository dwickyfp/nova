"""A period without data is answered as such, end to end, never as an empty result or an error.

Scripted provider frames drive the shared loop. A period that has not started is
refused before any query; a past period outside the data is detected from the
empty result and the first and last day the caller can see. Both turns end as an
answer naming the period, like a clarification, with no further data path tried.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import USER, setup_tool

PLAN = {
    "metrics": ["total_revenue"], "dimensions": [], "filters": [], "named_filters": [],
    "order_by": [], "limit": None, "unresolved_concepts": [],
}


async def run(provider, registry, question):
    context = LoopContext(user_name="alice", user=USER.copy())
    context.execution_now = datetime(2026, 11, 8, 3, tzinfo=UTC)
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="window", user_name="alice", title="Eval"),
            user_content=question, context=context, resolve_consent=allow,
        )
    ]
    return "".join(frames).replace(" ", "")


async def test_a_period_that_has_not_started_is_answered_without_a_query(monkeypatch):
    plan = {**PLAN, "time": {"dimension": "order_date", "grain": None, "range": "2026-12",
                             "compare": None}}
    tool, _planner, execute = setup_tool(monkeypatch, plan)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("w1", name="semantic_query",
                        arguments={"question": "omzet Desember 2026"}),
        text_frame("Desember 2026 belum berjalan, jadi belum ada datanya."),
    ])
    joined = await run(provider, registry, "Berapa omzet Desember 2026?")
    assert '"finish_reason":"clarification"' in joined
    assert "Desember2026belumberjalan" in joined
    assert '"finish_reason":"error"' not in joined
    execute.assert_not_awaited()


async def test_a_past_period_outside_the_data_names_the_covered_range(monkeypatch):
    from app.modules.query.service import query_service

    plan = {**PLAN, "time": {"dimension": "order_date", "grain": None,
                             "range": "2026-01", "compare": None}}
    tool, _planner, _execute = setup_tool(monkeypatch, plan)
    execute = AsyncMock(side_effect=[
        [SimpleNamespace(columns=["total_revenue"], rows=[[None]], error=None)],
        [SimpleNamespace(columns=["order_date", "total_revenue"], rows=[["2026-05-01", 3]],
                         error=None)],
        [SimpleNamespace(columns=["order_date", "total_revenue"], rows=[["2026-11-04", 5]],
                         error=None)],
    ])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    registry = ToolRegistry()
    registry.register(tool)
    # The model writes nothing: Nova's own answer names the range that exists.
    provider = ScriptedProvider(script=[
        tool_call_frame("w2", name="semantic_query", arguments={"question": "omzet Januari"}),
        text_frame(""),
    ])
    joined = await run(provider, registry, "Berapa omzet Januari 2026?")
    assert '"finish_reason":"clarification"' in joined
    assert "2026-05-01" in joined and "2026-11-04" in joined
    # One query for the answer and two for the window; nothing else was tried.
    assert execute.await_count == 3
