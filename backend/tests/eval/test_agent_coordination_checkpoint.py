"""A child sees a coordination message after a tool, without persisting it as user memory."""

from __future__ import annotations

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import (
    RecordingTool,
    ScriptedProvider,
    text_frame,
    thread,
    tool_call_frame,
)
from tests.eval.harness import TurnResult


@pytest.mark.asyncio
async def test_child_coordination_message_arrives_before_next_model_call() -> None:
    mailbox: list[str] = []
    snapshots: list[list[dict]] = []

    class FindingTool(RecordingTool):
        async def run(self, invocation, context):
            mailbox.append("Finance found Jakarta Enterprise down 22%.")
            return await super().run(invocation, context)

    class Provider(ScriptedProvider):
        async def stream(self, **kwargs):
            snapshots.append(list(kwargs["messages"]))
            async for frame in super().stream(**kwargs):
                yield frame

    provider = Provider([tool_call_frame("query"), text_frame("Updated analysis")])
    tool = FindingTool()
    registry = ToolRegistry()
    registry.register(tool)
    loop = AssistantLoop(provider=provider, registry=registry, system_prompt="Specialist")
    child_thread = thread(read_only_grant=True)

    async def checkpoint() -> list[str]:
        ready = list(mailbox)
        mailbox.clear()
        return ready

    async def consent(*args):
        return True

    frames = [
        frame
        async for frame in loop.run(
            thread=child_thread,
            user_content="Analyze revenue",
            context=LoopContext(user_name="bench", thread_id=child_thread.thread_id),
            resolve_consent=consent,
            on_checkpoint=checkpoint,
        )
    ]
    assert TurnResult(frames=frames).finish_reason == "stop"
    assert tool.runs == 1
    assert len(snapshots) == 2
    assert "Finance found Jakarta Enterprise down 22%." in str(snapshots[1])
    assert "Finance found Jakarta Enterprise down 22%." not in str(child_thread.messages)
