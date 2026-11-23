"""Studio agents are autonomous: they never ask permission to run a tool.

Owner decision (2026-11-23): a Studio run emits no approval request for any
tool (MCP, custom HTTP, automations, other actions). Questions about intent
still go to the person. Neva and unattended runs keep their gate. Every
autonomous call is audited as the caller, with redacted arguments; an action's
intent is recorded before it runs.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import router as agents_router
from app.modules.assistant import service as assistant_service
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.unit.test_assistant_consent_ownership import _signed_in
from tests.unit.test_assistant_stage_b import (
    FakeProvider,
    StubTool,
    _collect,
    _frame_event,
    _thread,
    _tool_call,
)

USER = {
    **_signed_in("alice"), "active_role": "MARKETING", "roles": ["MARKETING"],
    "assigned_roles": ["MARKETING"], "security_context_version": 7,
}


def _context() -> LoopContext:
    return LoopContext(user_name="alice", user=dict(USER), role="MARKETING", thread_id="t1")


def _script(args: dict) -> FakeProvider:
    return FakeProvider([
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("c1", args=args)]},
        {"role": "assistant", "content": "Done."},
    ])


@pytest.fixture
def audit(monkeypatch):
    recorded = AsyncMock(return_value="q1")
    monkeypatch.setattr("app.common.audit.write_audit_log", recorded)
    return recorded


async def _run(loop: AssistantLoop, resolver) -> list[str]:
    return await _collect(loop.run(
        thread=_thread(), user_content="Schedule it", context=_context(),
        resolve_consent=resolver,
    ))


async def test_studio_loop_runs_an_action_without_asking(audit):
    tool = StubTool(classification="destructive")
    registry = ToolRegistry()
    registry.register(tool)
    asked: list[str] = []

    async def resolver(_inv, classification):
        asked.append(classification)
        return False

    frames = await _run(
        AssistantLoop(provider=_script({"sql": "SELECT 1"}), registry=registry,
                      approval_gate=False),
        resolver,
    )
    names = [_frame_event(frame) for frame in frames]
    assert asked == []
    assert "input_required" not in names
    assert len(tool.invocations) == 1


async def test_neva_and_unattended_loops_keep_the_gate(audit):
    tool = StubTool(classification="destructive")
    registry = ToolRegistry()
    registry.register(tool)
    asked: list[str] = []

    async def resolver(_inv, classification):
        asked.append(classification)
        return False

    frames = await _run(AssistantLoop(provider=_script({"sql": "SELECT 1"}), registry=registry),
                        resolver)
    assert asked == ["destructive"]
    assert "input_required" in [_frame_event(frame) for frame in frames]
    assert tool.invocations == []
    audit.assert_not_called()


async def test_autonomous_action_is_audited_as_the_caller_before_and_after(audit):
    tool = StubTool(classification="destructive")
    tool.parameters["properties"]["api_token"] = {"type": "string"}
    registry = ToolRegistry()
    registry.register(tool)
    await _run(
        AssistantLoop(provider=_script({"sql": "SELECT 1", "api_token": "x"}),
                      registry=registry, approval_gate=False),
        AsyncMock(return_value=False),
    )
    statuses = [call.kwargs["status"] for call in audit.await_args_list]
    assert statuses == ["STARTED", "SUCCESS"]
    for call in audit.await_args_list:
        record = call.kwargs
        assert record["event_type"] == "AGENT_TOOL_CALL"
        assert record["user_name"] == "alice"
        assert record["active_role"] == "MARKETING"
        assert record["security_context_version"] == 7
        assert record["session_id"] == "t1"
        assert record["action"] == "DESTRUCTIVE"
        assert '"sql": "SELECT 1"' in record["sql_text"]
        assert '"api_token": "***"' in record["sql_text"]


def test_audited_arguments_mask_secret_keys_and_secret_shaped_values():
    secret = "sk-abcdefghijklmnopqrstuvwx"
    text = assistant_service._audited_arguments(
        {"password": "hunter2", "note": f"use {secret}", "month": "2025-07", "long": "x" * 9000}
    )
    assert secret not in text and "hunter2" not in text
    assert '"month": "2025-07"' in text
    assert len(text) <= 4000


async def test_autonomous_read_is_audited_once_after_it_runs(audit):
    tool = StubTool(classification="read_only")
    registry = ToolRegistry()
    registry.register(tool)
    await _run(
        AssistantLoop(provider=_script({"sql": "SELECT 1"}), registry=registry,
                      approval_gate=False),
        AsyncMock(return_value=False),
    )
    assert [call.kwargs["status"] for call in audit.await_args_list] == ["SUCCESS"]


async def test_an_audit_outage_does_not_stop_the_turn(monkeypatch):
    monkeypatch.setattr("app.common.audit.write_audit_log",
                        AsyncMock(side_effect=RuntimeError("audit down")))
    tool = StubTool(classification="destructive")
    registry = ToolRegistry()
    registry.register(tool)
    frames = await _run(
        AssistantLoop(provider=_script({"sql": "SELECT 1"}), registry=registry,
                      approval_gate=False),
        AsyncMock(return_value=False),
    )
    assert len(tool.invocations) == 1
    assert "done" in [_frame_event(frame) for frame in frames]


def test_the_studio_stream_builds_its_loop_without_the_gate():
    """The interactive Studio turn is the one surface that runs without approval."""
    source = inspect.getsource(agents_router)
    start = source.index("    loop = AssistantLoop(\n        provider=assistant_provider")
    loop_call = source[start:]
    loop_call = loop_call[: loop_call.index("\n    )\n")]
    assert "approval_gate=False" in loop_call


def test_unattended_agent_turns_keep_the_gate():
    """Automations and research branches still refuse actions instead of running them."""
    from app.modules.agents import turns

    assert "approval_gate" not in inspect.getsource(turns)
    assert assistant_service.AssistantLoop.__init__.__kwdefaults__["approval_gate"] is True
