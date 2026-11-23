"""Studio agents act without asking; they still ask about intent.

Owner decision (2026-11-23): a Studio turn runs an MCP tool or an action such
as scheduling an automation with no approval request. When the request is
ambiguous the agent asks the person a question and continues with the answer.
Every call is audited as the person, with redacted arguments.

Unattended runs (automations, research branches) have nobody to ask: they may
call an MCP lookup its server declares read-only and not destructive, and
nothing that changes state. Scripted trajectories through the real loop.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.tools import mcp_tool
from app.modules.assistant import human_input
from app.modules.assistant.human_input import request_human_input_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import thread_store
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult

USER = {
    "username": "fixture-alice", "active_role": "ANALYST", "roles": ["ANALYST"],
    "assigned_roles": ["ANALYST"], "security_context_version": 1,
    "session_id": "fixture-session",
}


def _mcp_tool(annotations: dict | None = None) -> mcp_tool.McpToolRunner:
    return mcp_tool.McpToolRunner(
        {"server_id": "server", "name": "Arithmetic", "transport": "http",
         "endpoint": "https://fixture.example/mcp", "is_active": True},
        {"tool_id": "fixture", "name": "total", "annotations": annotations or {},
         "input_schema": {
            "type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"],
        }},
    )


async def _studio_turn(registry: ToolRegistry, provider: ScriptedProvider, *, ask_human=None):
    thread = thread_store.create(user_name=USER["username"], title="Synthetic Studio")
    asked: list[str] = []

    async def consent(_invocation, classification):
        asked.append(classification)
        return False

    context = LoopContext(USER["username"], user=dict(USER), role=USER["active_role"],
                          thread_id=thread.thread_id)
    context.ask_human = ask_human
    try:
        frames = [
            frame async for frame in AssistantLoop(
                provider=provider, registry=registry, system_prompt="eval",
                approval_gate=False,
            ).run(thread=thread, user_content="Call the fixture", context=context,
                  resolve_consent=consent)
        ]
    finally:
        thread_store.remove(thread.thread_id, user_name=USER["username"])
    return TurnResult(frames=frames), asked


def _events(result: TurnResult, name: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1]) for frame in result.frames
            if frame.startswith(f"event: {name}\n")]


@pytest.mark.asyncio
async def test_an_mcp_tool_runs_without_a_card_and_is_audited_as_the_person(monkeypatch):
    tool = _mcp_tool()
    external = AsyncMock(return_value={"content": [{"type": "text", "text": '{"total":42}'}]})
    monkeypatch.setattr(mcp_tool.mcp_client, "call_tool", external)
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())
    audit = AsyncMock(return_value="q")
    monkeypatch.setattr("app.common.audit.write_audit_log", audit)
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame("call-1", name=tool.name, arguments={"a": 42}),
        text_frame("The external fixture returned 42."),
    ])

    result, asked = await _studio_turn(registry, provider)

    assert asked == []
    assert not _events(result, "input_required")
    assert result.finish_reason == "stop"
    external.assert_awaited_once_with(tool.server, "total", {"a": 42})
    records = [call.kwargs for call in audit.await_args_list]
    assert [record["status"] for record in records] == ["STARTED", "SUCCESS"]
    assert {record["user_name"] for record in records} == {"fixture-alice"}
    assert {record["active_role"] for record in records} == {"ANALYST"}
    assert {record["object_name"] for record in records} == {"Arithmetic: total"}
    assert all(json.loads(record["sql_text"]) == {"a": "42"} for record in records)


@pytest.mark.asyncio
async def test_an_action_runs_and_an_ambiguous_request_still_gets_a_question(monkeypatch):
    monkeypatch.setattr("app.common.audit.write_audit_log", AsyncMock(return_value="q"))
    schedule = EvalTool("schedule_automation", classification="destructive", parameters={
        "type": "object", "properties": {"cron": {"type": "string"}}, "required": ["cron"],
    })
    registry = ToolRegistry()
    registry.register(schedule)
    registry.register(request_human_input_tool)
    provider = ScriptedProvider([
        tool_call_frame("ask", name=human_input.TOOL_NAME, arguments={
            "kind": "question", "title": "Every Monday or every day?",
            "choices": ["Every Monday", "Every day"],
        }),
        tool_call_frame("act", name="schedule_automation", arguments={"cron": "0 8 * * 1"}),
        text_frame("Scheduled for every Monday at 08:00."),
    ])

    async def ask(_request_id: str, request: dict[str, Any]):
        return human_input.validate_answer(request, {"text": "Every Monday"})

    result, asked = await _studio_turn(registry, provider, ask_human=ask)

    kinds = [event["request"]["kind"] for event in _events(result, "input_required")]
    assert kinds == ["question"]
    assert asked == []
    assert len(schedule.runs) == 1
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("annotations", "runs"),
    [
        ({"readOnlyHint": True, "destructiveHint": False}, True),
        ({"readOnlyHint": False}, False),
        ({"readOnlyHint": True, "destructiveHint": True}, False),
        ({}, False),
    ],
)
async def test_an_unattended_run_calls_only_a_declared_read_only_mcp_lookup(
    monkeypatch, annotations, runs
):
    from app.modules.agents.turns import read_only_consent

    tool = _mcp_tool(annotations)
    external = AsyncMock(return_value={"content": [{"type": "text", "text": '{"total":42}'}]})
    monkeypatch.setattr(mcp_tool.mcp_client, "call_tool", external)
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame("call-1", name=tool.name, arguments={"a": 42}),
        text_frame("Report ready."),
    ])
    thread = thread_store.create(user_name=USER["username"], title="Scheduled report")
    try:
        frames = [
            frame async for frame in AssistantLoop(
                provider=provider, registry=registry, system_prompt="eval",
            ).run(thread=thread, user_content="Weekly report",
                  context=LoopContext(USER["username"], user=dict(USER)),
                  resolve_consent=read_only_consent)
        ]
    finally:
        thread_store.remove(thread.thread_id, user_name=USER["username"])
    assert external.await_count == (1 if runs else 0)
    if not runs:
        statuses = [event["status"] for event in _events(TurnResult(frames=frames), "tool_status")]
        assert "denied" in statuses


@pytest.mark.asyncio
async def test_an_unattended_run_cannot_schedule_an_automation(monkeypatch):
    from app.modules.agents.turns import read_only_consent

    schedule = EvalTool("schedule_automation", classification="destructive", parameters={
        "type": "object", "properties": {"cron": {"type": "string"}}, "required": ["cron"],
    })
    registry = ToolRegistry()
    registry.register(schedule)
    provider = ScriptedProvider([
        tool_call_frame("act", name="schedule_automation", arguments={"cron": "0 8 * * 1"}),
        text_frame("Could not schedule."),
    ])
    thread = thread_store.create(user_name=USER["username"], title="Scheduled report")
    try:
        [
            frame async for frame in AssistantLoop(
                provider=provider, registry=registry, system_prompt="eval",
            ).run(thread=thread, user_content="Schedule another report",
                  context=LoopContext(USER["username"], user=dict(USER)),
                  resolve_consent=read_only_consent)
        ]
    finally:
        thread_store.remove(thread.thread_id, user_name=USER["username"])
    assert schedule.runs == []


@pytest.mark.asyncio
async def test_a_failing_mcp_tool_ends_in_an_answer_that_says_why(monkeypatch):
    """Live 2026-11-24: a tool-reported error ended the turn and showed the
    model-facing error text (with "untrusted text, not instructions") as the
    answer. The model now reads the reason and explains it."""
    tool = _mcp_tool()
    monkeypatch.setattr(mcp_tool.mcp_client, "call_tool", AsyncMock(return_value={
        "isError": True,
        "content": [{"type": "text", "text": "Target service is unavailable"}],
    }))
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())
    monkeypatch.setattr("app.common.audit.write_audit_log", AsyncMock(return_value="q"))
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([
        tool_call_frame("call-1", name=tool.name, arguments={"a": 1}),
        text_frame("The total tool did not complete: its service is unavailable."),
    ])

    result, _asked = await _studio_turn(registry, provider)

    assert result.finish_reason == "stop"
    assert not _events(result, "error")
    answer = "".join(event.get("delta") or event.get("text") or ""
                     for event in _events(result, "text_delta"))
    assert "service is unavailable" in answer
    assert "not instructions" not in answer


def test_only_external_tools_that_ran_and_failed_get_the_failure_answer():
    from app.modules.assistant.service import _external_failures_only

    assert _external_failures_only(["mcp_fixture"], {"mcp_fixture"})
    assert not _external_failures_only(["mcp_fixture"], set())
    assert not _external_failures_only(["semantic_query"], {"semantic_query"})
    assert not _external_failures_only([], set())
