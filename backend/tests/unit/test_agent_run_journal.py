"""Recovery and scope checks for persisted agent runs."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import router
from app.modules.agents.run_journal import AgentRunJournal
from app.modules.assistant import events
from app.modules.assistant.schemas import AgentMessageRequest


@pytest.mark.asyncio
async def test_stale_run_is_interrupted_only_inside_its_scope(monkeypatch):
    journal = AgentRunJournal()
    scope = dict(owner_name="alice", agent_id="sales", thread_id="thread", role_name="analyst")
    stale = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
    get = AsyncMock(side_effect=[
        {"status": "running", "updated_at": stale},
        {"status": "interrupted", "updated_at": stale},
    ])
    execute = AsyncMock()
    monkeypatch.setattr(journal, "get", get)
    monkeypatch.setattr("app.modules.agents.run_journal.db.execute_system", execute)

    assert await journal.interrupt_stale("run", **scope)
    sql, params = execute.call_args.args
    assert "owner_name = %s" in sql and "role_name = %s" in sql
    assert params[1:6] == ["run", "alice", "sales", "thread", "analyst"]


@pytest.mark.asyncio
async def test_recent_run_is_not_interrupted(monkeypatch):
    journal = AgentRunJournal()
    scope = dict(owner_name="alice", agent_id="sales", thread_id="thread", role_name="analyst")
    monkeypatch.setattr(journal, "get", AsyncMock(return_value={
        "status": "running", "updated_at": datetime.now(UTC).replace(tzinfo=None),
    }))
    execute = AsyncMock()
    monkeypatch.setattr("app.modules.agents.run_journal.db.execute_system", execute)
    assert not await journal.interrupt_stale("run", **scope)
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_reports_interrupted_run_without_resending_a_tool(monkeypatch):
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={"agent_id": "sales"}))
    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"thread_id": "thread"})
    )
    monkeypatch.setattr(router.run_journal, "get", AsyncMock(side_effect=[
        {"status": "running", "last_sequence": -1},
        {"status": "running", "last_sequence": -1},
        {"status": "interrupted", "last_sequence": -1},
    ]))
    monkeypatch.setattr(router.run_journal, "events_after", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.run_journal, "interrupt_stale", AsyncMock(return_value=True))
    response = await router.replay_agent_run(
        "sales", "thread", "run", -1,
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    frames = [frame async for frame in response.body_iterator]
    assert [frame.split("\n", 1)[0] for frame in frames] == ["event: error", "event: done"]
    assert "run_interrupted" in frames[0]
    assert '"finish_reason":"interrupted"' in frames[1]


@pytest.mark.asyncio
async def test_slow_subscriber_replays_bounded_queue_while_run_completes(monkeypatch):
    from app.modules.assistant.tools import ToolRegistry

    agent = {"agent_id": "sales", "name": "Sales", "owner_name": "alice"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={"title": "Sales"}))
    monkeypatch.setattr(
        router.agent_service, "build_loop_inputs",
        AsyncMock(return_value=(ToolRegistry(), "System", 60, None)),
    )
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Sales"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(router.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(router, "remember_user_message", AsyncMock(return_value=0))
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(router.run_journal, "start", AsyncMock())
    append = AsyncMock()
    finish = AsyncMock()
    monkeypatch.setattr(router.run_journal, "append_batch", append)
    monkeypatch.setattr(router.run_journal, "finish", finish)

    class ScriptedLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, **_kwargs):
            for index in range(200):
                yield events.text_delta(str(index))
            yield events.done("reply", finish_reason="stop")

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    response = await router.send_agent_message(
        "sales", "thread", AgentMessageRequest(content="Question"),
        SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    await asyncio.gather(*list(router._active_run_tasks))
    assert [frame async for frame in response.body_iterator] == []
    assert sum(len(call.args[1]) for call in append.call_args_list) == 201
    finish.assert_awaited_once()
