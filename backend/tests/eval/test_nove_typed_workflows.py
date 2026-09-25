"""Nove tool trajectories keep consent and postcondition claims tied to evidence."""

from __future__ import annotations

import json

import pytest

from app.modules.assistant.app_context import NoveAppContext
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry, role_access
from app.modules.assistant.tools.inspect_agent_configuration import (
    inspect_agent_configuration_tool,
)
from app.modules.assistant.tools.query_context import (
    inspect_query_error_tool,
    verify_query_repair_tool,
)
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import TurnResult


def _plan(tool_name: str) -> dict:
    return {
        "intent": "ui_operation",
        "tools": [tool_name],
        "required_tools": [tool_name],
        "skills": [],
        "ml_task": None,
    }


def _user(role: str = "ACCOUNTADMIN") -> dict:
    return {
        "username": "bench",
        "active_role": role,
        "assigned_roles": [role],
        "session_id": "session-1",
    }


async def _run(tool, context: LoopContext, prompt: str, arguments: dict, consent):
    registry = ToolRegistry()
    registry.register(tool)
    call = tool_call_frame("tool-1", name=tool.name, arguments=arguments)
    call["tool_calls"][0]["function"]["arguments"] = json.dumps(arguments)
    provider = ScriptedProvider(
        [
            call,
            text_frame("The requested operation succeeded."),
        ],
        turn_plan=_plan(tool.name),
    )
    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="test"
        ).run(
            thread=thread(),
            user_content=prompt,
            context=context,
            resolve_consent=consent,
        )
    ]
    return TurnResult(frames=frames)


@pytest.mark.asyncio
async def test_grant_without_verified_postcondition_never_composes_success(monkeypatch) -> None:
    writes = []
    audits = []

    class Service:
        async def list_roles(self):
            return [{"name": "analyst"}]

        async def grant_access(self, security, **kwargs):
            writes.append(kwargs)
            return {"id": 1}

        async def effective_access(self, *, principal, active_role, resource):
            return {"object_access": []}

    async def audit(**kwargs):
        audits.append(kwargs["status"])
        return "audit-1"

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(role_access, "access_control_service", Service())
    monkeypatch.setattr(role_access, "write_audit_log", audit)
    monkeypatch.setattr(role_access.asyncio, "sleep", no_sleep)
    approvals = []

    async def approve(invocation, classification):
        approvals.append((invocation.tool_name, classification))
        return True

    result = await _run(
        role_access.grant_role_access_tool,
        LoopContext(
            user_name="bench",
            user=_user(),
            app_context=NoveAppContext.model_validate({
                "version": 1,
                "surface": {"id": "roles.detail", "route": "/roles/analyst"},
                "entity": {"type": "role", "id": "analyst", "name": "analyst"},
            }),
        ),
        "Give this role SELECT on sales.orders",
        {"role": "analyst", "grants": [{"resource": "sales.orders", "access": "SELECT"}]},
        approve,
    )
    assert approvals == [("grant_role_access", "destructive")]
    assert len(writes) == 1
    assert audits == ["PENDING", "UNVERIFIED"]
    assert result.finish_reason == "error"
    assert "The requested operation succeeded." not in "".join(result.frames)


@pytest.mark.asyncio
async def test_current_query_error_is_inspected_without_consent() -> None:
    app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "workspace.sql", "route": "/workspaces"},
            "editor": {"documentId": "file-1"},
            "execution": {
                "type": "sql",
                "executionId": "exec-1",
                "status": "error",
                "errorMessage": "Unknown column revenue",
            },
            "events": [
                {
                    "id": "event-1",
                    "timestamp": "2026-09-24T10:00:00Z",
                    "source": "execution",
                    "type": "query_failed",
                    "surfaceId": "workspace.sql",
                    "executionId": "exec-1",
                    "status": "failure",
                    "payload": {"documentId": "file-1", "sql": "SELECT revenue FROM sales"},
                }
            ],
        }
    )

    async def unexpected_consent(_invocation, _classification):
        raise AssertionError("Current application evidence should not need consent")

    result = await _run(
        inspect_query_error_tool,
        LoopContext(user_name="bench", user=_user(), app_context=app),
        "Fix this query error",
        {},
        unexpected_consent,
    )
    assert result.finish_reason == "stop"
    assert "inspect_query_error" in "".join(result.frames)


@pytest.mark.asyncio
async def test_unlinked_query_repair_cannot_claim_success() -> None:
    app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "workspace.sql", "route": "/workspaces"},
            "execution": {"type": "sql", "executionId": "exec-2", "status": "success"},
            "events": [],
        }
    )

    async def unexpected_consent(_invocation, _classification):
        raise AssertionError("Verification reads should not need consent")

    result = await _run(
        verify_query_repair_tool,
        LoopContext(user_name="bench", user=_user(), app_context=app),
        "Did that fix it?",
        {},
        unexpected_consent,
    )
    assert result.finish_reason == "error"
    assert "The requested operation succeeded." not in "".join(result.frames)


@pytest.mark.asyncio
async def test_correlated_patch_and_successful_rerun_complete_repair() -> None:
    app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "workspace.sql", "route": "/workspaces"},
            "editor": {"documentId": "file-1"},
            "execution": {"type": "sql", "executionId": "exec-2", "status": "success"},
            "events": [
                {
                    "id": "patch",
                    "timestamp": "2026-09-24T10:00:00Z",
                    "source": "assistant",
                    "type": "editor_patch_applied",
                    "surfaceId": "workspace.sql",
                    "correlationId": "artifact-1",
                    "status": "success",
                    "payload": {"documentId": "file-1", "persisted": True},
                },
                {
                    "id": "rerun",
                    "timestamp": "2026-09-24T10:00:01Z",
                    "source": "execution",
                    "type": "query_completed",
                    "surfaceId": "workspace.sql",
                    "executionId": "exec-2",
                    "correlationId": "artifact-1",
                    "status": "success",
                    "payload": {"documentId": "file-1"},
                },
            ],
        }
    )

    async def unexpected_consent(_invocation, _classification):
        raise AssertionError("Verification reads should not need consent")

    result = await _run(
        verify_query_repair_tool,
        LoopContext(user_name="bench", user=_user(), app_context=app),
        "Did the patch fix this query?",
        {"correlation_id": "artifact-1"},
        unexpected_consent,
    )
    assert result.finish_reason == "stop"
    assert "verify_query_repair" in "".join(result.frames)


@pytest.mark.asyncio
async def test_studio_configuration_inspection_never_uses_studio_conversation(monkeypatch) -> None:
    from app.modules.assistant.tools import inspect_agent_configuration as module

    class Repository:
        async def get_agent(self, agent_id, *, owner_name):
            assert (agent_id, owner_name) == ("agent-1", "bench")
            return {
                "name": "Sales Analyst",
                "default_tools": ["query_execute"],
                "semantic_model_ids": [],
                "instructions_response": "private Studio agent instruction",
            }

    async def audit(**kwargs):
        return "audit-1"

    monkeypatch.setattr(module, "agent_repository", Repository())
    monkeypatch.setattr(module, "write_audit_log", audit)
    app = NoveAppContext.model_validate(
        {
            "version": 1,
            "surface": {"id": "studio.agent", "route": "/studio"},
            "entity": {"type": "studio_agent", "id": "agent-1"},
        }
    )

    async def unexpected_consent(_invocation, _classification):
        raise AssertionError("Owner-scoped configuration reads should not need consent")

    result = await _run(
        inspect_agent_configuration_tool,
        LoopContext(user_name="bench", user=_user(), app_context=app),
        "Why can't this agent create charts?",
        {},
        unexpected_consent,
    )
    assert result.finish_reason == "stop"
    assert "private Studio agent instruction" not in "".join(result.frames)
