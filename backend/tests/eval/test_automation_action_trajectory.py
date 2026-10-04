"""Automation dispatch uses the shared loop's per-call consent and recovery path."""

from unittest.mock import AsyncMock

import pytest

from app.modules.agents import router
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread, ConsentPolicy
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence.actions import BusinessActionTool
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult
from tests.unit.test_automation_actions import automation_program as automation_program
from tests.unit.test_business_actions import action_program as action_program
from tests.unit.test_business_actions import operation
from tests.unit.test_business_actions import program as program
from tests.unit.test_intelligence_engine import USER


@pytest.mark.parametrize("allowed", [True, False, None])
async def test_automation_trajectory_requires_fresh_consent(
    automation_program, monkeypatch, allowed
):
    env = automation_program
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    registry = ToolRegistry()
    tool = BusinessActionTool(env.service)
    registry.register(tool)
    prompts = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return allowed

    provider = ScriptedProvider(
        [
            tool_call_frame(
                "automation-consent",
                name=tool.name,
                arguments={
                    "action_id": env.action.id,
                    **operation(env.action).model_dump(),
                },
            ),
            text_frame("The report automation configuration is verified."),
        ],
        turn_plan={
            "intent": "ui_operation",
            "tools": [tool.name],
            "required_tools": [tool.name],
            "skills": [],
            "ml_task": None,
        },
    )
    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider,
                registry=registry,
                system_prompt="test",
            ).run(
                thread=AssistantThread("thread", "alice", "Business", consent=ConsentPolicy(True)),
                user_content="Schedule the reviewed automation.",
                context=LoopContext("alice", user=USER, thread_id="thread"),
                resolve_consent=consent,
            )
        ]
    )
    assert prompts == [(tool.name, "destructive")]
    assert len(env.store.creations) == (1 if allowed else 0)
    assert result.finish_reason == (
        "stop" if allowed else "denied" if allowed is False else "cancelled"
    )


async def test_uncertain_automation_trajectory_never_redispatches(automation_program, monkeypatch):
    env = automation_program
    env.store.fail_after_create = True
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    registry = ToolRegistry()
    tool = BusinessActionTool(env.service)
    registry.register(tool)

    async def consent(_invocation, _classification):
        return True

    for attempt in range(2):
        provider = ScriptedProvider(
            [
                tool_call_frame(
                    f"automation-retry-{attempt}",
                    name=tool.name,
                    arguments={
                        "action_id": env.action.id,
                        **operation(env.action).model_dump(),
                    },
                ),
                text_frame("The report automation is verified."),
            ],
            turn_plan={
                "intent": "ui_operation",
                "tools": [tool.name],
                "required_tools": [tool.name],
                "skills": [],
                "ml_task": None,
            },
        )
        result = TurnResult(
            frames=[
                frame
                async for frame in AssistantLoop(
                    provider=provider,
                    registry=registry,
                    system_prompt="test",
                ).run(
                    thread=AssistantThread("thread", "alice", "Business"),
                    user_content="Execute the automation.",
                    context=LoopContext("alice", user=USER, thread_id="thread"),
                    resolve_consent=consent,
                )
            ]
        )
        assert result.finish_reason == "error"
        assert "The report automation is verified." not in "".join(result.frames)
    assert len(env.store.creations) == 1
    current = await env.service.get(env.action.id, USER)
    verified = await env.service.verify(current.id, USER, expected_revision=current.revision)
    assert verified.status == "verified"
