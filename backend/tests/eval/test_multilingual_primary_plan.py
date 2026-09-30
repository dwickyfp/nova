"""A question in any language costs one planner call and one answer call.

The turn planner routes the turn, fills the intent frame, and writes the first
query's plan in one call. The loop runs that query before the first model call,
and the user's own constraints (here "top 3", read from Japanese) hold.
"""

from __future__ import annotations

from app.modules.assistant.service import AssistantLoop
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame
from tests.unit.test_semantic_llm_planner import _context, _plan, setup_tool

QUESTION = "今年売上上位3都市"


async def test_japanese_top_three_runs_the_planned_query_and_answers_once(monkeypatch):
    tool, tool_planner, execute = setup_tool(monkeypatch)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[text_frame("Jakarta が 1 位です。")], turn_plan={
        "intent": "semantic_analytics", "tools": ["semantic_query"],
        "required_tools": ["semantic_query"], "ml_task": None,
        # The planner's rewrite dropped the ranking; the frame keeps the user's words.
        "intent_frame": {"language": "ja", "range": "current_year", "top_n": 3,
                         "order": "desc"},
        "primary_plan": _plan(dimensions=["city"]),
        "primary_view": None,
    })
    context = _context(question=QUESTION)
    context.agent_scope = {"agent": "Sales"}
    context.authorized_semantic_models = []
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="ja", user_name="alice", title="Eval"),
            user_content=QUESTION, context=context, resolve_consent=allow,
        )
    ]
    joined = "".join(frames).replace(" ", "")
    assert '"finish_reason":"stop"' in joined
    assert provider.calls == 1  # the answer; the query needed no model call
    tool_planner.complete.assert_not_awaited()
    sql = execute.call_args.kwargs["sql"]
    assert "LIMIT 3" in sql and "DESC" in sql
    assert context.intent_frame.language == "ja"
