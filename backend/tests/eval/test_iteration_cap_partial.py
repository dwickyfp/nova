"""At the step limit with verified results in hand, the turn says it is partial.

Re-bench diagnosis-001 ended on the 16-step limit with verified tables already
retrieved and no answer text at all; the reader saw only the limit error.
"""

from __future__ import annotations

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_llm_planner import _plan, setup_tool


async def _cap_turn(monkeypatch, *, rows: bool) -> tuple[list[str], str]:
    tool, _planner, execute = setup_tool(monkeypatch, *([_plan(dimensions=["city"])] * 3))
    if not rows:
        execute.side_effect = RuntimeError("engine busy")
    registry = ToolRegistry()
    registry.register(tool)
    question = "Explain the July revenue decline by city."
    provider = ScriptedProvider(script=[
        tool_call_frame(f"s{index}", name="semantic_query", arguments={"question": question})
        for index in range(3)
    ])
    context = LoopContext(user_name="alice", user=USER.copy())
    context.agent_scope = {"agent": "Sales Analyst"}
    context.agent_id = "agent-1"
    frames = [
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, max_iterations=2,
        ).run(
            thread=AssistantThread(thread_id="cap", user_name="alice", title="Cap"),
            user_content=question, context=context, resolve_consent=allow,
        )
    ]
    text = "".join(frame for frame in frames if frame.startswith("event: text_delta"))
    return frames, text


async def test_verified_results_at_the_limit_end_with_an_honest_partial_note(monkeypatch):
    frames, text = await _cap_turn(monkeypatch, rows=True)
    assert any('"finish_reason":"iteration_cap"' in f.replace(" ", "") for f in frames)
    assert "reached the step limit for this answer before I could finish" in text


async def test_without_verified_results_the_limit_adds_no_claim(monkeypatch):
    frames, text = await _cap_turn(monkeypatch, rows=False)
    assert any('"finish_reason":"iteration_cap"' in f.replace(" ", "") for f in frames)
    assert "step limit" not in text
