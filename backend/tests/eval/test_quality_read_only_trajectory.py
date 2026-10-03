"""Evaluation uses the shared loop while enforcing read-only invocation effects."""

from unittest.mock import AsyncMock

import pytest

from app.modules.agents.quality import evaluation_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult


class MixedTool:
    name = "read_or_write"
    description = "A tool with an invocation-dependent effect."
    classification = "read_only"
    requires_consent = False
    parameters = {
        "type": "object",
        "properties": {"write": {"type": "boolean"}},
        "required": ["write"],
        "additionalProperties": False,
    }

    def __init__(self):
        self.execute = AsyncMock(return_value=ToolOutcome(ok=True, summary="Read complete"))

    def classification_for(self, invocation):
        return "destructive" if invocation.arguments["write"] else "read_only"

    def preview(self, invocation):
        return "Write" if invocation.arguments["write"] else "Read"

    async def run(self, invocation, context):
        return await self.execute(invocation, context)


@pytest.mark.parametrize("write", [True, False])
async def test_evaluation_cannot_bypass_read_only_with_consent_free_tool(write):
    tool = MixedTool()
    registry = ToolRegistry()
    registry.register(tool)
    bounded = evaluation_registry(registry)
    provider = ScriptedProvider(
        [
            tool_call_frame("effect", name=tool.name, arguments={"write": write}),
            text_frame("The evaluation finished."),
        ],
        turn_plan={
            "intent": "ui_operation",
            "tools": [tool.name],
            "required_tools": [tool.name],
            "skills": [],
            "ml_task": None,
        },
    )
    consent = AsyncMock(return_value=True)
    context = LoopContext("alice", steps=[])
    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider,
                registry=bounded,
                system_prompt="test",
                iterative=True,
                max_iterations=3,
            ).run(
                thread=AssistantThread("evaluation", "alice", "Quality"),
                user_content="Evaluate this capability",
                context=context,
                resolve_consent=consent,
            )
        ]
    )
    consent.assert_not_awaited()
    assert tool.execute.await_count == (0 if write else 1)
    assert provider.calls <= 3
    if write:
        assert result.finish_reason == "error"
        assert "Evaluation permits read-only tools only" in "".join(result.frames)
    else:
        assert result.finish_reason == "stop"
