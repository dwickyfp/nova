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


@pytest.fixture(autouse=True)
def run_write_coordination(monkeypatch):
    from app.core.redis import session_store
    from tests.unit.test_agent_harness import _EventRedis

    monkeypatch.setattr(session_store, "_redis", _EventRedis())


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
    monkeypatch.setattr(router.run_journal, "has_security_version", AsyncMock(return_value=True))
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
@pytest.mark.parametrize("cancel", [False, "provider", "journal"])
async def test_slow_subscriber_replays_bounded_queue_while_run_completes(monkeypatch, cancel):
    from app.core.redis import session_store
    from app.modules.assistant.tools import ToolRegistry

    monkeypatch.setattr(session_store, "_redis", SimpleNamespace(
        get=AsyncMock(return_value="1" if cancel else None),
    ))

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
    monkeypatch.setattr(
        router.assistant_repository, "learning_enabled", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(router.run_journal, "start", AsyncMock())
    append = AsyncMock()
    if cancel == "journal":
        async def delay_first_flush(*_args):
            if append.await_count == 1:
                await asyncio.sleep(3600)
        append.side_effect = delay_first_flush
    finish = AsyncMock()
    monkeypatch.setattr(router.run_journal, "append_batch", append)
    monkeypatch.setattr(router.run_journal, "finish", finish)

    class ScriptedLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, **_kwargs):
            if cancel == "provider":
                await asyncio.sleep(3600)
            if cancel == "journal":
                yield events.format_sse("tool_call", {"tool_call_id": "tool", "status": "running"})
                await asyncio.sleep(3600)
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
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == (2 if cancel == "journal" else 1 if cancel else 0)
    assert sum(len(call.args[1]) for call in append.call_args_list) == (
        3 if cancel == "journal" else 1 if cancel else 201
    )
    assert finish.await_args.args[1] == ("cancelled" if cancel else "completed")
    if cancel:
        assert '"finish_reason":"cancelled"' in frames[-1]
    saved = router.assistant_repository.append_message.await_args.kwargs
    assert {
        "kind": "turn_outcome", "finish_reason": "cancelled" if cancel else "stop",
    } in saved["steps"]


@pytest.mark.parametrize("valid_scope", [False, True])
async def test_direct_cancel_requires_current_security_context(monkeypatch, valid_scope):
    from fastapi import HTTPException

    from app.core.redis import session_store

    user = {"username": "alice", "active_role": "analyst", "roles": ["analyst"],
            "security_context_version": 3}
    monkeypatch.setattr(router, "_require_agent", AsyncMock())
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock())
    monkeypatch.setattr(router.run_journal, "get", AsyncMock(return_value={"status": "running"}))
    version = AsyncMock(return_value=valid_scope)
    monkeypatch.setattr(router.run_journal, "has_security_version", version)
    redis = SimpleNamespace(set=AsyncMock())
    monkeypatch.setattr(session_store, "_redis", redis)
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    if valid_scope:
        result = await router.cancel_agent_run("sales", "thread", "run", user)
        assert result["cancelling"]
        redis.set.assert_awaited_once_with("nova:agent-run:cancel:run", "1", ex=3600)
    else:
        with pytest.raises(HTTPException) as exc:
            await router.cancel_agent_run("sales", "thread", "run", user)
        assert exc.value.status_code == 404
        redis.set.assert_not_awaited()
    version.assert_awaited_once_with("run", 3)


async def test_current_run_query_is_bound_to_role_and_security_version(monkeypatch):
    execute = AsyncMock(return_value={"rows": [["run"]]})
    monkeypatch.setattr("app.modules.agents.run_journal.db.execute_system", execute)
    assert await AgentRunJournal().current(
        owner_name="alice", agent_id="sales", thread_id="thread", role_name="analyst",
        security_version=3,
    ) == "run"
    sql, params = execute.call_args.args
    assert "status='running'" in sql
    assert params == ["alice", "sales", "thread", "analyst", 3]
