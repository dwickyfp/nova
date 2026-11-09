"""Studio keeps working when the model planner fails; Neva still stops."""

from __future__ import annotations

from app.modules.assistant.planning import TurnPlanningError
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.benchmark.studio_accuracy.model import bench_model
from tests.eval.harness import EvalTool


class BrokenPlanner(ScriptedProvider):
    async def plan_turn(self, **_kwargs):
        raise TurnPlanningError("planner unavailable")


def semantic_tool():
    return EvalTool(
        "semantic_query",
        parameters={"type": "object", "properties": {"question": {"type": "string"}},
                    "required": ["question"]},
        outcomes=[ToolOutcome(
            ok=True, summary="1 row",
            table={"columns": ["total_revenue"], "rows": [[1500000]]},
            data={"semantic_plan": {"metrics": ["total_revenue"]}, "sql": "SELECT 1"},
        )],
    )


async def run(question, *, studio=True, views=True):
    tool = semantic_tool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = BrokenPlanner(script=[
        tool_call_frame("s1", name="semantic_query", arguments={"question": question}),
        text_frame("Penjualan bulan ini 1.500.000."),
    ])
    context = LoopContext(user_name="alice")
    if studio:
        context.agent_scope = {"agent": "Sales"}
        context.authorized_semantic_models = [{"_scoped_ir": bench_model()}] if views else []
    thread = AssistantThread(thread_id="fallback", user_name="alice", title="Eval")
    thread.consent.always_allow_read_only = True
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread, user_content=question, context=context, resolve_consent=allow,
        )
    ]
    return "".join(frames).replace(" ", ""), tool, context


async def test_governed_metric_question_falls_back_to_the_semantic_tool():
    joined, tool, context = await run("Berapa penjualan bulan ini?")
    assert '"finish_reason":"stop"' in joined
    assert len(tool.runs) == 1
    assert any(step.get("planner_fallback") == "semantic_analytics"
               for step in context.steps or [])


async def test_the_fallback_does_not_read_the_question_in_any_language():
    # Without the planner, the governed tool plans the question itself and says
    # what the catalog cannot cover; no word list decides the route.
    for question in ("Tolong bantu saya dong", "今月の売上は？"):
        joined, tool, context = await run(question)
        assert "planning_failed" not in joined
        assert len(tool.runs) == 1
        assert any(step.get("planner_fallback") == "semantic_analytics"
                   for step in context.steps or [])


async def test_a_studio_agent_without_a_view_falls_back_to_clarification():
    joined, tool, _context = await run("Berapa penjualan bulan ini?", views=False)
    assert tool.runs == []
    assert "planning_failed" not in joined


async def test_neva_falls_back_to_a_read_only_plan_when_planning_fails():
    # Neva does not stop with nothing: a safe plan of read-only lookups, with
    # nothing required, replaces the plan the model could not shape.
    joined, _tool, context = await run("Berapa penjualan bulan ini?", studio=False)
    assert "planning_failed" not in joined
    assert any(step.get("planner_fallback") == "direct_answer"
               for step in context.steps or [])
