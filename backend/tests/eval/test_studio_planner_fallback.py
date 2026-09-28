"""Studio keeps working when the model planner fails; Nove still stops."""

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


async def run(question, *, studio=True):
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
        context.authorized_semantic_models = [{"_scoped_ir": bench_model()}]
    thread = AssistantThread(thread_id="fallback", user_name="alice", title="Eval")
    thread.consent.always_allow_read_only = True
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=thread, user_content=question, context=context, resolve_consent=allow,
        )
    ]
    return "".join(frames).replace(" ", ""), tool, context


async def test_governed_metric_question_falls_back_to_the_semantic_tool():
    # "jelaskan" keeps the question off the fast path, so the planner is asked.
    joined, tool, context = await run("Berapa penjualan bulan ini? jelaskan")
    assert '"finish_reason":"stop"' in joined
    assert len(tool.runs) == 1
    assert any(step.get("planner_fallback") == "semantic_analytics"
               for step in context.steps or [])


async def test_a_fully_resolved_metric_question_skips_the_model_planner():
    joined, tool, context = await run("Berapa penjualan bulan ini?")
    assert '"finish_reason":"stop"' in joined
    assert len(tool.runs) == 1
    assert any(step.get("planner_fast_path") == "semantic_analytics"
               for step in context.steps or [])
    assert not any(step.get("planner_fallback") for step in context.steps or [])


async def test_unmatched_question_falls_back_to_clarification():
    joined, tool, _context = await run("Tolong bantu saya dong")
    assert tool.runs == []
    assert "planning_failed" not in joined


async def test_nove_still_stops_when_planning_fails():
    joined, tool, _context = await run("Berapa penjualan bulan ini?", studio=False)
    assert '"finish_reason":"planning_failed"' in joined
    assert tool.runs == []
