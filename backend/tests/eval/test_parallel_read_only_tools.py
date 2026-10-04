"""Independent read-only calls from one model response overlap; output order holds."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.config import settings
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame
from tests.eval.harness import EvalTool

DELAY = 0.3


class SlowSemantic(EvalTool):
    def __init__(self, classification: str = "read_only") -> None:
        super().__init__(
            "semantic_query", classification=classification,
            parameters={"type": "object", "properties": {"question": {"type": "string"}},
                        "required": ["question"]},
        )
        self.started: list[str] = []

    async def run(self, invocation, context):
        question = invocation.arguments["question"]
        self.started.append(question)
        self.runs.append(invocation)
        await asyncio.sleep(DELAY)
        context.last_result = {"columns": ["q"], "rows": [[question]]}
        return ToolOutcome(
            ok=True, summary=question,
            table={"title": question, "columns": ["label", "total_revenue"],
                   "rows": [[question, 100 if question == "a" else 200]]},
            data={"semantic_plan": {"metrics": ["total_revenue"]}, "sql": "SELECT 1"},
        )


def two_calls() -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "semantic_query", "arguments": '{"question": "a"}'}},
            {"id": "c2", "type": "function",
             "function": {"name": "semantic_query", "arguments": '{"question": "b"}'}},
        ],
    }


async def run(tool, *, iterative: bool):
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        script=[two_calls(), text_frame("a 100, b 200.")],
        turn_plan={"intent": "semantic_analytics", "tools": ["semantic_query"],
                   "required_tools": ["semantic_query"], "ml_task": None},
    )
    thread = AssistantThread(thread_id="parallel", user_name="alice", title="Eval")
    thread.consent.always_allow_read_only = True

    async def allow(_invocation, _classification):
        return True

    context = LoopContext(user_name="alice")
    started = time.perf_counter()
    frames = [
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, iterative=iterative, max_calls_per_tool=6,
        ).run(thread=thread, user_content="a dan b", context=context, resolve_consent=allow)
    ]
    return time.perf_counter() - started, frames, context


async def test_two_read_only_calls_overlap_and_keep_their_order():
    tool = SlowSemantic()
    elapsed, frames, context = await run(tool, iterative=True)
    assert elapsed < DELAY * 1.8
    # Execution overlaps (start order is not meaningful); output order is.
    assert sorted(invocation.arguments["question"] for invocation in tool.runs) == ["a", "b"]
    tables = [frame for frame in frames if frame.startswith("event: table")]
    assert len(tables) == 2 and '"a"' in tables[0] and '"b"' in tables[1]
    # The later call's result is the turn's latest result, as in serial order.
    assert context.last_result == {"columns": ["q"], "rows": [["b"]]}
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")


async def test_non_iterative_loop_stays_serial():
    tool = SlowSemantic()
    elapsed, _frames, _context = await run(tool, iterative=False)
    assert elapsed >= DELAY * 2


async def test_calls_that_need_approval_are_not_started_early():
    tool = SlowSemantic(classification="destructive")
    elapsed, _frames, _context = await run(tool, iterative=True)
    assert elapsed >= DELAY * 2 or len(tool.runs) < 2


async def test_workflow_flag_keeps_independent_reads_concurrent_with_a_barrier(monkeypatch):
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)

    class SynchronizedSemantic(SlowSemantic):
        def __init__(self):
            super().__init__()
            self.arrived = set()
            self.both_started = asyncio.Event()

        async def run(self, invocation, context):
            self.arrived.add(invocation.tool_call_id)
            if len(self.arrived) == 2:
                self.both_started.set()
            await self.both_started.wait()
            return await super().run(invocation, context)

    tool = SynchronizedSemantic()
    # Serial execution cannot release this barrier; the bound only detects a deadlock.
    async with asyncio.timeout(5):
        _, frames, context = await run(tool, iterative=True)
    assert tool.both_started.is_set() and len(tool.runs) == 2
    assert len([frame for frame in frames if frame.startswith("event: table")]) == 2
    assert len([step for step in context.steps if step.get("kind") == "tool"]) == 2


@pytest.mark.parametrize("mission,hook", [("mission", None), (None, lambda *args: None)])
def test_mission_or_active_ordered_hook_prevents_early_dispatch(mission, hook):
    registry = ToolRegistry()
    tool = SlowSemantic()
    registry.register(tool)
    loop = AssistantLoop(provider=ScriptedProvider([]), registry=registry, iterative=True)
    context = LoopContext(user_name="alice", mission_id=mission, business_result_hook=hook)
    thread = AssistantThread(thread_id="serial", user_name="alice", title="Eval")
    thread.consent.always_allow_read_only = True
    assert loop._prefetch(two_calls()["tool_calls"][0], context, thread,
                          ("semantic_query",), {}, set()) is None
    assert not tool.runs
