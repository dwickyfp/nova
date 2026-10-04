"""Reviewed actions pass through the actual bounded loop and consent boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread, ConsentPolicy
from app.modules.assistant.tools import ToolRegistry
from app.modules.intelligence.actions import BusinessActionTool
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult
from tests.unit.test_business_actions import action_program as action_program
from tests.unit.test_business_actions import operation
from tests.unit.test_business_actions import program as program
from tests.unit.test_intelligence_engine import USER as ACTION_USER

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "roles": ["ANALYST"],
    "security_context_version": 3,
    "session_id": "s-alice",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False, None])
async def test_action_trajectory_requires_per_call_consent_even_with_read_grant(
    monkeypatch, allowed
):
    from app.modules.agents import router
    from app.modules.assistant import tool_gate

    dispatch = AsyncMock(return_value=SimpleNamespace(id="action", status="verified", revision=5))
    service = SimpleNamespace(
        dispatch=dispatch,
        get=AsyncMock(
            return_value=SimpleNamespace(
                configuration=SimpleNamespace(agent_id="finance"),
            )
        ),
    )
    tool = BusinessActionTool(service)
    registry = ToolRegistry()
    registry.register(tool)
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    shared_gate = AsyncMock(wraps=tool_gate.resolve_tool_consent)
    monkeypatch.setattr(tool_gate, "resolve_tool_consent", shared_gate)
    provider = ScriptedProvider(
        [
            tool_call_frame(
                "action-call",
                name=tool.name,
                arguments={
                    "action_id": "action",
                    "operation_id": "execute-1",
                    "expected_revision": 3,
                    "thread_id": "action-thread",
                },
            ),
            text_frame("The monitor and its schedule were verified."),
        ],
        turn_plan={
            "intent": "ui_operation",
            "tools": [tool.name],
            "required_tools": [tool.name],
            "skills": [],
            "ml_task": None,
        },
    )
    prompts = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        return allowed

    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider,
                registry=registry,
                system_prompt="test",
            ).run(
                thread=AssistantThread(
                    "action-thread", "alice", "Business", consent=ConsentPolicy(True)
                ),
                user_content="Execute the reviewed monitor action",
                context=LoopContext("alice", user=USER, thread_id="action-thread"),
                resolve_consent=consent,
            )
        ]
    )
    assert prompts == [(tool.name, "destructive")]
    assert dispatch.await_count == (1 if allowed else 0)
    shared_gate.assert_awaited_once()
    if allowed:
        assert result.finish_reason == "stop"
        assert dispatch.call_args.args[2] == USER
    else:
        assert result.finish_reason == ("denied" if allowed is False else "cancelled")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["uncertain_dispatch", "policy_revoked"])
async def test_action_failure_trajectory_stops_without_claiming_success(
    action_program, monkeypatch, failure
):
    from app.modules.agents import router

    service, action, _, adapter, _, policy, _, _ = action_program
    adapter.fail_after = failure == "uncertain_dispatch"
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    tool = BusinessActionTool(service)
    registry = ToolRegistry()
    registry.register(tool)
    prompts = []

    async def consent(invocation, classification):
        prompts.append((invocation.tool_name, classification))
        if failure == "policy_revoked":
            policy.revision += 1
        return True

    for attempt in range(2 if failure == "uncertain_dispatch" else 1):
        provider = ScriptedProvider(
            [
                tool_call_frame(
                    f"action-call-{attempt}",
                    name=tool.name,
                    arguments={"action_id": action.id, **operation(action).model_dump()},
                ),
                text_frame("The action was verified successfully."),
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
                    provider=provider, registry=registry, system_prompt="test"
                ).run(
                    thread=AssistantThread("thread", "alice", "Business"),
                    user_content="Execute the reviewed action",
                    context=LoopContext("alice", user=ACTION_USER, thread_id="thread"),
                    resolve_consent=consent,
                )
            ]
        )
        assert result.finish_reason == "error"
        assert "tool_failed" in result.error_codes
        assert "The action was verified successfully." not in "".join(result.frames)
    assert prompts == [(tool.name, "destructive")] * (
        2 if failure == "uncertain_dispatch" else 1
    )
    assert len(adapter.effects) == (1 if failure == "uncertain_dispatch" else 0)
    if failure == "uncertain_dispatch":
        saved = await service.get(action.id, ACTION_USER)
        assert saved.status == "verification_required" and saved.receipt is None
