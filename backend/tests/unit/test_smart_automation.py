"""Scheduled reports through Smart: a proposal, a confirmed automation, a delegated run."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from app.modules.agents import automations, harness_worker
from app.modules.agents.automations import AutomationRunner
from app.modules.agents.harness_worker import (
    AgentHarnessWorker,
    AuthenticationUnavailable,
    _root_artifacts,
)
from app.modules.agents.identity import AUTOMATION_SESSION, SMART_AGENT_ID
from app.modules.agents.tools.propose_automation import propose_automation_tool
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.query.service import delegated_connection_for
from tests.unit.test_agent_automations import NOW, automation

WEEKLY = {
    "title": "Weekly expense", "prompt": "Berapa total expense minggu lalu?",
    "schedule_kind": "cron", "schedule_expr": "0 8 * * 1", "timezone": "Asia/Jakarta",
}


def smart(**overrides):
    return automation(agent_id=SMART_AGENT_ID, **overrides)


def run(**overrides):
    return {
        "run_id": "root", "owner_name": "alice", "role_name": "analyst", "thread_id": "thread",
        "session_id": AUTOMATION_SESSION + "au1", "security_version": 1, **overrides,
    }


def propose(**arguments):
    return ToolInvocation(
        tool_call_id="p", tool_name="propose_automation", arguments={**WEEKLY, **arguments},
    )


@pytest.mark.asyncio
async def test_a_proposal_is_validated_and_writes_nothing(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(automations.automation_repository, "create", create)
    context = LoopContext("alice")
    outcome = await propose_automation_tool.run(propose(
        condition={"metric": "total_expense", "operator": ">", "value": 5_000_000_000},
    ), context)
    assert outcome.ok and outcome.data["created"] is False
    assert context.automation_proposal["schedule_expr"] == "0 8 * * 1"
    assert context.automation_proposal["condition"]["operator"] == ">"
    assert context.automation_proposal["next_run"]
    assert "delivery" not in context.automation_proposal
    create.assert_not_awaited()
    assert propose_automation_tool.classification == "read_only"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"schedule_expr": "* * * * *"}, {"schedule_expr": "not cron"}, {"prompt": "x"},
])
async def test_an_invalid_schedule_is_not_proposed(arguments):
    context = LoopContext("alice")
    outcome = await propose_automation_tool.run(propose(**arguments), context)
    assert not outcome.ok and outcome.recoverable
    assert context.automation_proposal is None


def test_a_proposal_travels_with_the_answer_after_its_table_and_chart():
    steps = [{"kind": "table", "columns": ["a"], "rows": [[1]]}]
    shown = _root_artifacts(steps, {"title": "Weekly expense"})
    assert [item["kind"] for item in shown] == ["table", "automation_proposal"]
    assert shown[1]["title"] == "Weekly expense"
    assert [item["kind"] for item in _root_artifacts(steps)] == ["table"]


@pytest.mark.asyncio
async def test_a_scheduled_run_is_its_automation_for_as_long_as_that_stands(monkeypatch):
    get = AsyncMock(return_value=smart())
    monkeypatch.setattr(automations.automation_repository, "get", get)
    user = await AgentHarnessWorker._automation_user(run())
    get.assert_awaited_once_with("au1", owner_name="alice")
    assert user["username"] == "alice" and user["active_role"] == "analyst"
    assert user["assigned_roles"] == ["analyst"]
    assert user["session_id"] == AUTOMATION_SESSION + "au1"
    assert user["encrypted_password"] == "delegated"


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [
    None,
    smart(enabled=False),
    smart(role_name="admin"),
    automation(agent_id="finance"),
])
async def test_a_scheduled_run_ends_when_its_automation_no_longer_authorizes_it(
    monkeypatch, stored
):
    monkeypatch.setattr(automations.automation_repository, "get", AsyncMock(return_value=stored))
    with pytest.raises(AuthenticationUnavailable):
        await AgentHarnessWorker._automation_user(run())


@pytest.mark.asyncio
async def test_a_login_session_never_takes_the_scheduled_path(monkeypatch):
    lookup = AsyncMock(return_value=smart())
    monkeypatch.setattr(automations.automation_repository, "get", lookup)
    monkeypatch.setattr(harness_worker.session_store, "get", AsyncMock(return_value=None))
    worker = AgentHarnessWorker(SimpleNamespace())
    with pytest.raises(AuthenticationUnavailable):
        await worker._user_for(run(session_id="3f6b61dd-f531-4f88-bf35-e1552bc2bf54"))
    lookup.assert_not_awaited()
    async with worker._identity(run(session_id="3f6b61dd-f531-4f88-bf35-e1552bc2bf54")):
        assert delegated_connection_for("alice") is None


@pytest.mark.asyncio
async def test_a_scheduled_run_fails_closed_without_the_worker_execution_account(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", "")
    worker = AgentHarnessWorker(SimpleNamespace())
    with pytest.raises(AuthenticationUnavailable):
        async with worker._identity(run()):
            raise AssertionError("no participant may run without the delegated connection")


@pytest.mark.asyncio
async def test_a_scheduled_run_executes_as_its_owner_under_the_automation_role(monkeypatch):
    from contextlib import asynccontextmanager

    from app.core.config import settings
    from app.modules.task_orchestration import execution

    connection = object()
    prepared = []

    class Executor:
        def __init__(self, *_args, **kwargs):
            self.account = kwargs

        @asynccontextmanager
        async def owner_connection(self, owner):
            assert owner == "alice"
            yield connection

        async def _prepare_task_session(self, _cursor, spec):
            prepared.append(spec.active_role)

    @asynccontextmanager
    async def cursor(_connection):
        yield object()

    monkeypatch.setattr(settings, "WORKER_IMPERSONATION_USER", "nova_worker")
    monkeypatch.setattr(execution, "DelegateExecutor", Executor)
    monkeypatch.setattr(execution, "_dict_cursor", cursor)
    worker = AgentHarnessWorker(SimpleNamespace())
    async with worker._identity(run()):
        assert delegated_connection_for("alice") is connection
        assert delegated_connection_for("bob") is None
    assert prepared == ["analyst"]
    assert delegated_connection_for("alice") is None


def journal(monkeypatch, status, *, tables=None, answer="Total expense was 5.1 billion."):
    from app.modules.agents import harness_repository as repository_module
    from app.modules.assistant import repository as assistant_module

    root_id = "root-1"
    final_id = str(uuid5(NAMESPACE_URL, f"nova:auto:final:{root_id}"))
    created = AsyncMock(return_value={"run_id": root_id})
    monkeypatch.setattr(repository_module.harness_repository, "create_root", created)
    monkeypatch.setattr(repository_module.harness_repository, "get", AsyncMock(return_value={
        "run_id": root_id, "status": status,
        "checkpoint": {"verified_evidence": {"tables": tables or {}}},
    }))
    monkeypatch.setattr(assistant_module.assistant_repository, "create_thread",
                        AsyncMock(return_value={"thread_id": "t1"}))
    append = AsyncMock(return_value={"message_id": "q1"})
    monkeypatch.setattr(assistant_module.assistant_repository, "append_message", append)
    monkeypatch.setattr(assistant_module.assistant_repository, "list_messages", AsyncMock(
        return_value=[{"message_id": final_id, "content": answer}],
    ))
    return created, append


@pytest.mark.asyncio
async def test_the_runner_queues_a_smart_root_marked_with_its_automation(monkeypatch):
    created, append = journal(monkeypatch, "completed")
    monkeypatch.setattr(automations, "_deliver", AsyncMock(return_value="delivered:studio"))
    executor = SimpleNamespace(owner_connection=AsyncMock(side_effect=AssertionError("no data")))
    result = await AutomationRunner(executor)._run(smart(), NOW)
    assert result.status == "delivered:studio" and result.thread_id == "t1"
    assert result.text == "Total expense was 5.1 billion."
    root = created.await_args.kwargs
    assert root["session_id"] == AUTOMATION_SESSION + "au1"
    assert (root["owner_name"], root["role_name"]) == ("alice", "analyst")
    assert root["objective"] == "Berapa penjualan minggu lalu?"
    # The runner stores the question; the Smart worker stores the answer.
    assert [call.kwargs["role"] for call in append.await_args_list] == ["user"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("condition", "tables", "status"), [
    ({"metric": "total_expense", "operator": ">", "value": 5}, {}, "condition_unavailable"),
    ({"metric": "total_expense", "operator": ">", "value": 5},
     {"e1": {"columns": ["total_expense"], "rows": [["3"]]}}, "condition_not_met"),
])
async def test_an_alert_is_judged_on_the_smart_runs_result_tables(
    monkeypatch, condition, tables, status
):
    journal(monkeypatch, "completed", tables=tables)
    deliver = AsyncMock()
    monkeypatch.setattr(automations, "_deliver", deliver)
    result = await AutomationRunner(SimpleNamespace())._run(smart(condition=condition), NOW)
    assert result.status == status
    deliver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
async def test_a_smart_run_that_does_not_complete_is_a_failed_fire(monkeypatch, status):
    journal(monkeypatch, status)
    result = await AutomationRunner(SimpleNamespace())._run(smart(), NOW)
    assert result.status == f"failed:smart_{status}" and result.text == ""


@pytest.mark.asyncio
async def test_a_smart_run_that_outlasts_its_window_times_out(monkeypatch):
    from app.core.config import settings

    journal(monkeypatch, "running")
    monkeypatch.setattr(settings, "SMART_MAX_WALL_TIME", -60)
    result = await AutomationRunner(SimpleNamespace())._run(smart(), NOW)
    assert result.status == "failed:smart_timeout"


@pytest.mark.asyncio
async def test_anyone_schedules_their_own_smart_and_only_the_owner_another_agent(monkeypatch):
    from app.modules.agents import router

    user = {"username": "alice", "session_id": "s", "active_role": "analyst",
            "assigned_roles": ["analyst"], "security_context_version": 1}
    agent = await router._require_automation_agent(SMART_AGENT_ID, user)
    assert (agent["agent_id"], agent["owner_name"]) == (SMART_AGENT_ID, "alice")
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={"owner_name": "bob"}))
    with pytest.raises(HTTPException) as denied:
        await router._require_automation_agent("finance", user)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException):
        await router._require_automation_agent(SMART_AGENT_ID, {"username": "alice"})
