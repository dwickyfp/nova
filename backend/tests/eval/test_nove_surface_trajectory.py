"""Current Nova surface facts must win when a Nove conversation changes topic."""

from __future__ import annotations

import json

import pytest

from app.modules.assistant.app_context import NoveAppContext
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool


@pytest.mark.asyncio
async def test_role_deictic_request_after_workspace_surface_switch() -> None:
    role_tool = EvalTool(
        "inspect_role_access",
        parameters={
            "type": "object",
            "properties": {"role": {"type": "string"}},
            "required": ["role"],
            "additionalProperties": False,
        },
        summary="ANALYST has no SELECT grant for orders",
    )
    role_tool.requires_consent = False
    registry = ToolRegistry()
    registry.register(role_tool)

    class ContextCheckingProvider(ScriptedProvider):
        async def stream(self, *, messages, tools=None, provider=None):
            app_prompt = next(
                message["content"]
                for message in messages
                if "<NOVA_APPLICATION_CONTEXT>" in str(message["content"])
            )
            turn_prompt = next(
                message["content"]
                for message in messages
                if "<NOVA_TURN_CONTEXT>" in str(message["content"])
            )
            assert '"id":"ANALYST"' in app_prompt
            assert '"surfaceId":"roles.detail"' in app_prompt
            assert '"objective":"Analyze revenue"' not in turn_prompt
            async for item in super().stream(messages=messages, tools=tools, provider=provider):
                yield item

    provider = ContextCheckingProvider(
        script=[
            tool_call_frame(
                "role-1",
                name="inspect_role_access",
                arguments={"role": "ANALYST"},
            ),
            text_frame("ANALYST lacks the requested table access."),
        ],
        turn_plan={
            "intent": "ui_operation",
            "tools": ["inspect_role_access"],
            "required_tools": ["inspect_role_access"],
            "skills": [],
            "ml_task": None,
        },
    )
    thread = AssistantThread(thread_id="t", user_name="alice", title="T")
    thread.messages.append(
        AssistantMessage(
            message_id="old",
            role="assistant",
            content="Revenue was down.",
            steps=[
                {
                    "kind": "active_state",
                    "state": {
                        "surface_id": "workspace.sql",
                        "objective": "Analyze revenue",
                        "last_evidence": ["revenue-evidence"],
                    },
                }
            ],
        )
    )
    app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "roles.detail", "route": "/roles/ANALYST"},
            "entity": {"type": "role", "id": "ANALYST", "name": "ANALYST"},
        }
    )
    context = LoopContext(user_name="alice", app_context=app)

    async def no_consent(*_args):
        raise AssertionError("The read-only contextual action should not prompt")

    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="Nove"
        ).run(
            thread=thread,
            user_content="Why can't this role read orders?",
            context=context,
            resolve_consent=no_consent,
        )
    ]
    assert [call.arguments["role"] for call in role_tool.runs] == ["ANALYST"]
    assert context.active_state["surface_id"] == "roles.detail"
    assert context.active_state["last_evidence"] != ["revenue-evidence"]
    assert any(
        json.loads(frame.split("data: ", 1)[1])["finish_reason"] == "stop"
        for frame in frames
        if frame.startswith("event: done")
    )
