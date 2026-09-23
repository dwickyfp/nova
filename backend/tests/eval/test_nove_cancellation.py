"""Cancellation releases a tool waiting without progress events."""

import asyncio

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult


@pytest.mark.asyncio
async def test_cancel_during_silent_tool_releases_task():
    started = asyncio.Event()
    stopped = asyncio.Event()
    released = asyncio.Event()

    class WaitingTool(EvalTool):
        async def run(self, invocation, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

    registry = ToolRegistry()
    registry.register(WaitingTool("query_execute"))
    loop = AssistantLoop(
        provider=ScriptedProvider([tool_call_frame("query")]),
        registry=registry,
        system_prompt="test",
    )
    context = LoopContext(user_name="bench")

    async def collect():
        return [
            frame
            async for frame in loop.run(
                thread=thread(read_only_grant=True),
                user_content="SELECT 1",
                context=context,
                resolve_consent=allow,
                cancelled=stopped.is_set,
            )
        ]

    task = asyncio.create_task(collect())
    await started.wait()
    stopped.set()
    frames = await task
    assert TurnResult(frames=frames).finish_reason == "cancelled"
    assert released.is_set()
    assert context.tool_progress_sink is None
