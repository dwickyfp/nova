"""Scheduling from Studio states only what the scheduler returned.

Benchmark ``automation-002``: both scheduling attempts failed and the answer was
empty. ``automation-003``/``-004``: the answer named a first run and a time zone
(WIB) that the tool result did not establish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.automations import AutomationError, automation_repository
from app.modules.agents.tools.schedule_automation import schedule_automation_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult

USER = {
    "username": "owner", "active_role": "ANALYST", "roles": ["ANALYST"],
    "assigned_roles": ["ANALYST"], "security_context_version": 1, "session_id": "s1",
}
ARGS = {
    "title": "Ringkasan pendapatan harian", "prompt": "Ringkasan pendapatan kemarin",
    "schedule_kind": "cron", "schedule_expr": "0 7 * * *", "timezone": "Asia/Jakarta",
}


def _context() -> LoopContext:
    context = LoopContext("owner", user=dict(USER), role="ANALYST")
    context.agent_id = "agent-1"
    context.agent_owner_name = "owner"
    context.agent_scope = {"agent_id": "agent-1"}
    return context


@pytest.mark.asyncio
async def test_the_result_names_the_next_run_in_the_schedule_time_zone(monkeypatch):
    monkeypatch.setattr(automation_repository, "create", AsyncMock(return_value={
        "automation_id": "a1", "title": ARGS["title"], "timezone": "Asia/Jakarta",
        "next_run_at": datetime(2026, 11, 25, 0, 0, tzinfo=UTC),
    }))
    outcome = await schedule_automation_tool.run(
        ToolInvocation("c1", "schedule_automation", dict(ARGS)), _context())
    assert outcome.ok
    assert outcome.data["timezone"] == "Asia/Jakarta"
    assert outcome.data["next_run_at"] == "2026-11-25T00:00:00+00:00"
    assert outcome.data["next_run_local"] == "2026-11-25 07:00"
    assert "Next run: 2026-11-25 07:00 (Asia/Jakarta)" in outcome.summary
    assert "do not work out other dates or zones" in outcome.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [2, 3])
async def test_a_schedule_that_keeps_failing_says_nothing_was_scheduled(monkeypatch, attempts):
    monkeypatch.setattr("app.common.audit.write_audit_log", AsyncMock(return_value="q"))
    monkeypatch.setattr(automation_repository, "create", AsyncMock(
        side_effect=AutomationError("Automations run at most every 60 minutes.")))
    registry = ToolRegistry()
    registry.register(schedule_automation_tool)
    provider = ScriptedProvider([
        *(tool_call_frame(f"s{n}", name="schedule_automation", arguments=dict(ARGS))
          for n in range(attempts)),
        text_frame("Done, it runs every morning."),
        text_frame("Done, it runs every morning."),
        text_frame("Done, it runs every morning."),
    ], turn_plan={"intent": "ui_operation", "tools": ["schedule_automation"],
                  "required_tools": ["schedule_automation"], "ml_task": None})
    frames = [
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="eval", approval_gate=False,
        ).run(
            thread=AssistantThread(thread_id="auto-1", user_name="owner", title="Eval"),
            user_content="Kirimkan ringkasan pendapatan harian setiap pagi jam 7.",
            context=_context(), resolve_consent=lambda *_: None,
        )
    ]
    result = TurnResult(frames=frames)
    answer = "".join(
        frame for frame in frames if frame.startswith("event: text_delta"))
    assert "Nothing was scheduled. Automations run at most every 60 minutes." in answer
    assert "every morning" not in answer
    # Repairs ran out: the turn ends failed, and says so in plain words.
    assert result.finish_reason == (
        "required_capability_incomplete" if attempts == 2 else "error")



@pytest.mark.asyncio
async def test_a_refused_first_call_shows_its_reason_and_the_repair_schedules(monkeypatch):
    """automation-001/002/004: the first call failed with "The step could not be
    completed", so the agent could not see what to change."""
    monkeypatch.setattr("app.common.audit.write_audit_log", AsyncMock(return_value="q"))
    create = AsyncMock(return_value={
        "automation_id": "a1", "title": ARGS["title"], "timezone": "Asia/Jakarta",
        "next_run_at": datetime(2026, 11, 25, 0, 0, tzinfo=UTC),
    })
    monkeypatch.setattr(automation_repository, "create", create)
    registry = ToolRegistry()
    registry.register(schedule_automation_tool)
    provider = ScriptedProvider([
        tool_call_frame("s1", name="schedule_automation", arguments={**ARGS, "title": ""}),
        tool_call_frame("s2", name="schedule_automation", arguments=dict(ARGS)),
        text_frame("Ringkasan dijadwalkan setiap hari pukul 07.00 (Asia/Jakarta); "
                   "jadwal berikutnya 2026-11-25 07:00."),
    ], turn_plan={"intent": "ui_operation", "tools": ["schedule_automation"],
                  "required_tools": ["schedule_automation"], "ml_task": None})
    frames = [
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="eval", approval_gate=False,
        ).run(
            thread=AssistantThread(thread_id="auto-2", user_name="owner", title="Eval"),
            user_content="Kirimkan ringkasan pendapatan harian setiap pagi jam 7.",
            context=_context(), resolve_consent=lambda *_: None,
        )
    ]
    result = TurnResult(frames=frames)
    failed = [frame for frame in frames
              if frame.startswith("event: tool_status") and '"failed"' in frame]
    assert len(failed) == 1
    assert "title: String should have at least 1 character." in failed[0]
    assert "could not be completed" not in failed[0]
    assert create.await_count == 1
    assert result.finish_reason == "stop"
