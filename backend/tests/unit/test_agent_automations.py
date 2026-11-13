"""Studio automations: schedule validation, conditions, delegated runs, delivery, claims."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import automations
from app.modules.agents.automations import (
    AutomationError,
    AutomationRunner,
    AutomationWorker,
    condition_met,
    next_run,
)
from app.modules.query.service import delegated_connection_for

NOW = datetime(2026, 9, 28, 1, 0, tzinfo=UTC)
TABLES = {"e1": {"columns": ["total_revenue"], "rows": [["9000000.00"]]}}


def automation(**overrides):
    return {
        "automation_id": "au1", "agent_id": "a1", "owner_name": "alice", "role_name": "analyst",
        "title": "Weekly revenue", "prompt": "Berapa penjualan minggu lalu?",
        "schedule_kind": "cron", "schedule_expr": "0 8 * * 1", "timezone": "Asia/Jakarta",
        "condition": None, "delivery": {}, "enabled": True, "next_run_at": "2026-09-28 01:00:00",
        **overrides,
    }


def test_cron_next_run_is_in_the_owner_timezone():
    fire = next_run("cron", "0 8 * * 1", "Asia/Jakarta", NOW)
    assert fire == datetime(2026, 10, 5, 1, 0, tzinfo=UTC)  # Monday 08:00 WIB


@pytest.mark.parametrize(
    ("kind", "expression"),
    [("cron", "* * * * *"), ("interval", "EVERY(INTERVAL 5 MINUTE)"), ("cron", "not cron")],
)
def test_too_frequent_or_invalid_schedules_are_refused(kind, expression):
    with pytest.raises(AutomationError):
        next_run(kind, expression, "Asia/Jakarta", NOW)


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (None, True),
        ({"metric": "total_revenue", "operator": "<", "value": 10_000_000}, True),
        ({"metric": "total_revenue", "operator": ">", "value": 10_000_000}, False),
        ({"metric": "order_count", "operator": ">", "value": 1}, None),
    ],
)
def test_conditions_compare_the_result_metric(condition, expected):
    assert condition_met(condition, TABLES) is expected


class FakeExecutor:
    def __init__(self):
        self.owners = []

    def owner_connection(self, owner):
        @asynccontextmanager
        async def connection():
            self.owners.append(owner)
            @asynccontextmanager
            async def cursor(*_args, **_kwargs):
                yield SimpleNamespace()
            yield SimpleNamespace(name="impersonated", cursor=cursor)
        return connection()

    async def _prepare_task_session(self, cursor, spec):
        assert spec.active_role == "analyst"


@pytest.fixture
def run_env(monkeypatch):
    from app.modules.agents.repository import agent_repository
    from app.modules.assistant.repository import assistant_repository

    monkeypatch.setattr(agent_repository, "get_agent", AsyncMock(return_value={
        "agent_id": "a1", "owner_name": "alice",
    }))
    monkeypatch.setattr(assistant_repository, "create_thread",
                        AsyncMock(return_value={"thread_id": "t1"}))
    append = AsyncMock()
    monkeypatch.setattr(assistant_repository, "append_message", append)
    seen = {}

    async def run_turn(agent, item, thread_id):
        seen["delegated"] = delegated_connection_for("alice")
        seen["other_user"] = delegated_connection_for("mallory")
        return "Penjualan minggu lalu 9.000.000.", [{"kind": "answer"}], TABLES

    monkeypatch.setattr(automations, "_run_turn", run_turn)
    repository = SimpleNamespace(record_run=AsyncMock())
    return seen, append, repository


async def test_a_run_executes_as_the_owner_and_lands_in_history(run_env):
    seen, append, repository = run_env
    executor = FakeExecutor()
    result = await AutomationRunner(executor, repository=repository).run(automation(), now=NOW)
    assert executor.owners == ["alice"]
    assert seen["delegated"].name == "impersonated"
    assert seen["other_user"] is None
    assert result.status == "delivered:inbox"
    assert result.thread_id == "t1"
    assert [call.kwargs["role"] for call in append.call_args_list] == ["user", "assistant"]
    repository.record_run.assert_not_awaited()


async def test_an_unmet_condition_is_recorded_but_not_delivered(run_env, monkeypatch):
    _seen, _append, repository = run_env
    deliver = AsyncMock()
    monkeypatch.setattr(automations, "_deliver", deliver)
    result = await AutomationRunner(FakeExecutor(), repository=repository).run(
        automation(condition={"metric": "total_revenue", "operator": ">", "value": 1e9}),
        now=NOW,
    )
    assert result.status == "condition_not_met"
    deliver.assert_not_awaited()


async def test_delivery_calls_the_owners_mcp_tool_with_the_answer(run_env, monkeypatch):
    from app.modules.agents import mcp_client
    from app.modules.agents.repository import agent_repository

    _seen, _append, repository = run_env
    monkeypatch.setattr(agent_repository, "get_tool", AsyncMock(return_value={
        "tool_id": "t9", "source": "mcp", "server_id": "s1", "name": "post_message",
    }))
    monkeypatch.setattr(agent_repository, "get_mcp_server",
                        AsyncMock(return_value={"server_id": "s1", "endpoint": "https://x"}))
    call = AsyncMock(return_value={"content": []})
    monkeypatch.setattr(mcp_client, "call_tool", call)
    prepared, receipt = AsyncMock(), AsyncMock()

    async def delivered(*_args):
        prepared.assert_awaited_once()
        return {"content": []}

    call.side_effect = delivered
    with automations.delivery_journal(prepared, receipt):
        result = await AutomationRunner(FakeExecutor(), repository=repository).run(
            automation(delivery={"mcp_tool_id": "t9", "text_argument": "text",
                                 "fixed_arguments": {"channel": "#sales"}}),
            now=NOW,
        )
    assert result.status == "delivered:inbox+external"
    assert call.call_args.args[2] == {"channel": "#sales",
                                      "text": "Penjualan minggu lalu 9.000.000."}
    assert prepared.call_args.args[0]["arguments"] == call.call_args.args[2]
    receipt.assert_awaited_once()


async def test_a_failed_run_returns_a_redacted_outcome_for_the_job_owner(run_env):
    _seen, _append, repository = run_env

    class Broken:
        def owner_connection(self, owner):
            raise RuntimeError("impersonation refused")

    result = await AutomationRunner(Broken(), repository=repository).run(automation(), now=NOW)
    assert result.status == "failed:RuntimeError"
    repository.record_run.assert_not_awaited()


async def test_worker_drains_durable_jobs_without_using_redis_claims(monkeypatch):
    monkeypatch.setattr(automations.automation_repository, "due",
                        AsyncMock(return_value=[automation()]))
    runner = SimpleNamespace(run=AsyncMock())
    redis = SimpleNamespace(set=AsyncMock(side_effect=RuntimeError("Redis unavailable")))
    worker = AutomationWorker(runner, redis)
    worker._jobs = SimpleNamespace(enqueue=AsyncMock(),
                                  claim=AsyncMock(side_effect=[{"id": "job"}, None]),
                                  run=AsyncMock())
    assert await worker.tick(NOW) == 1
    worker._jobs.enqueue.assert_awaited_once_with(automation())
    worker._jobs.run.assert_awaited_once_with({"id": "job"}, runner, NOW)
    redis.set.assert_not_awaited()


async def test_the_chat_tool_is_a_write_and_owner_only():
    from app.modules.agents.tools.schedule_automation import schedule_automation_tool
    from app.modules.assistant.tools import ToolInvocation, requires_consent

    assert schedule_automation_tool.classification == "destructive"
    assert requires_consent(schedule_automation_tool)
    outcome = await schedule_automation_tool.run(
        ToolInvocation("c1", "schedule_automation", {
            "title": "x", "prompt": "revenue?", "schedule_kind": "cron",
            "schedule_expr": "0 8 * * 1",
        }),
        SimpleNamespace(agent_id="a1", agent_owner_name="owner", user={"username": "viewer"}),
    )
    assert not outcome.ok and outcome.error_class == "PERMISSION_DENIED"
