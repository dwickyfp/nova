"""Recovery and scope checks for persisted agent runs."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataConflict, MetadataOutcomeUnknown
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents import router
from app.modules.agents import run_journal as module
from app.modules.agents.run_journal import AgentRunJournal
from app.modules.assistant import events
from app.modules.assistant.schemas import AgentMessageRequest


@pytest.fixture(autouse=True)
def run_write_coordination(monkeypatch):
    from app.core.redis import session_store
    from tests.unit.fake_lock_redis import FakeLockRedis

    monkeypatch.setattr(session_store, "_redis", FakeLockRedis())


@pytest.fixture
def journal_store(monkeypatch):
    store = AsyncMock()
    store.actor = SecurityContext("alice", "analyst", security_context_version=3)

    @asynccontextmanager
    async def scope(owner=None, *, read_only=False):
        assert owner in {None, "alice"}
        yield store

    monkeypatch.setattr(module, "_store", scope)
    return store


@pytest.mark.parametrize("same_scope", [False, True])
async def test_stale_run_is_interrupted_only_inside_its_scope(journal_store, same_scope):
    journal_store.get_run.return_value = {
        "agent_id": "sales",
        "thread_id": "thread",
        "role_name": "analyst" if same_scope else "other",
        "status": "running",
    }
    journal_store.interrupt_stale.return_value = True
    interrupted = await AgentRunJournal().interrupt_stale(
        "run",
        owner_name="alice",
        agent_id="sales",
        thread_id="thread",
        role_name="analyst",
    )
    assert interrupted is same_scope
    if same_scope:
        journal_store.interrupt_stale.assert_awaited_once_with("run", stale_after_seconds=60)
    else:
        journal_store.interrupt_stale.assert_not_awaited()


async def test_recent_run_is_not_interrupted(journal_store):
    journal_store.get_run.return_value = {
        "agent_id": "sales",
        "thread_id": "thread",
        "role_name": "analyst",
        "status": "running",
    }
    journal_store.interrupt_stale.return_value = False
    assert not await AgentRunJournal().interrupt_stale(
        "run",
        owner_name="alice",
        agent_id="sales",
        thread_id="thread",
        role_name="analyst",
    )
    journal_store.interrupt_stale.assert_awaited_once_with("run", stale_after_seconds=60)


@pytest.mark.parametrize("failure", [False, True, "unknown"])
async def test_finish_releases_claim_only_after_confirmed_commit(
    journal_store, monkeypatch, failure
):
    journal = AgentRunJournal()
    journal._claims["run"] = 4
    journal._role_changed.add("run")
    journal_store.finish_run.return_value = not failure
    if failure == "unknown":

        @asynccontextmanager
        async def unknown_commit(*args, **kwargs):
            journal_store.finish_run.return_value = True
            yield journal_store
            raise MetadataOutcomeUnknown()

        monkeypatch.setattr(module, "_store", unknown_commit)
    if failure:
        with pytest.raises(MetadataOutcomeUnknown if failure == "unknown" else MetadataConflict):
            await journal.finish("run", "completed")
        assert journal.claim_of("run") == 4 and "run" in journal._role_changed
    else:
        await journal.finish("run", "completed")
        assert journal.claim_of("run") is None and "run" not in journal._role_changed
    journal_store.finish_run.assert_awaited_once_with("run", "completed", generation=4)
    if failure is True:
        journal_store.block_replay_after_role_change.assert_not_awaited()
    else:
        journal_store.block_replay_after_role_change.assert_awaited_once_with("run")


@pytest.mark.asyncio
async def test_replay_reports_interrupted_run_without_resending_a_tool(monkeypatch):
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={"agent_id": "sales"}))
    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"thread_id": "thread"})
    )
    monkeypatch.setattr(
        router.run_journal,
        "get",
        AsyncMock(
            side_effect=[
                {"status": "running", "last_sequence": -1},
                {"status": "running", "last_sequence": -1},
                {"status": "interrupted", "last_sequence": -1},
            ]
        ),
    )
    monkeypatch.setattr(router.run_journal, "events_after", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.run_journal, "has_security_version", AsyncMock(return_value=True))
    monkeypatch.setattr(router.run_journal, "interrupt_stale", AsyncMock(return_value=True))
    response = await router.replay_agent_run(
        "sales",
        "thread",
        "run",
        -1,
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    frames = [frame async for frame in response.body_iterator]
    assert [frame.split("\n", 1)[0] for frame in frames] == ["event: error", "event: done"]
    assert "run_interrupted" in frames[0]
    assert '"finish_reason":"interrupted"' in frames[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, "provider", "journal"])
@pytest.mark.parametrize("precomposed", [False, True])
async def test_slow_subscriber_replays_bounded_queue_while_run_completes(
    monkeypatch,
    cancel,
    precomposed,
):
    from app.core.redis import session_store
    from app.modules.assistant.tools import ToolRegistry

    monkeypatch.setattr(
        session_store,
        "_redis",
        SimpleNamespace(
            get=AsyncMock(return_value="1" if cancel else None),
        ),
    )

    agent = {"agent_id": "sales", "name": "Sales", "owner_name": "alice"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={"title": "Sales"}))
    monkeypatch.setattr(
        router.agent_service,
        "build_loop_inputs",
        AsyncMock(return_value=(ToolRegistry(), "System", 60, None)),
    )
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Sales"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(router.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(
        router.assistant_repository,
        "learning_enabled",
        AsyncMock(return_value=False),
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
            if precomposed:
                frames = [events.text_delta(str(index)) for index in range(200)]
                frames.extend(
                    [
                        events.table({"columns": ["units"], "rows": [[42]]}),
                        events.chart("chart-tool", '{"type":"bar"}'),
                        events.done("reply", finish_reason="stop"),
                    ]
                )
                for frame in frames:
                    yield frame
            else:
                for index in range(200):
                    yield events.text_delta(str(index))
                yield events.done("reply", finish_reason="stop")

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    response = await router.send_agent_message(
        "sales",
        "thread",
        AgentMessageRequest(content="Question"),
        SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    await asyncio.gather(*list(router._active_run_tasks))
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == (2 if cancel == "journal" else 1 if cancel else 0)
    assert sum(len(call.args[1]) for call in append.call_args_list) == (
        3 if cancel == "journal" else 1 if cancel else 203 if precomposed else 201
    )
    assert finish.await_args.args[1] == ("cancelled" if cancel else "completed")
    if cancel:
        assert '"finish_reason":"cancelled"' in frames[-1]
    else:
        journaled = [frame for call in append.call_args_list for frame in call.args[1]]
        sequences = [json.loads(frame.split("data: ", 1)[1])["sequence"] for frame in journaled]
        assert all(left < right for left, right in zip(sequences, sequences[1:], strict=False))
        if precomposed:
            assert [frame.splitlines()[0] for frame in journaled[-3:]] == [
                "event: table",
                "event: chart",
                "event: done",
            ]
    saved = router.assistant_repository.append_message.await_args.kwargs
    assert {
        "kind": "turn_outcome",
        "finish_reason": "cancelled" if cancel else "stop",
    } in saved["steps"]


@pytest.mark.parametrize("valid_scope", [False, True])
async def test_direct_cancel_requires_current_security_context(monkeypatch, valid_scope):
    from fastapi import HTTPException

    from app.core.redis import session_store

    user = {
        "username": "alice",
        "active_role": "analyst",
        "roles": ["analyst"],
        "security_context_version": 3,
    }
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


async def test_current_run_query_is_bound_to_role_and_security_version(journal_store):
    journal_store.current_run.return_value = {"run_id": "run"}
    journal = AgentRunJournal()
    scope = dict(
        owner_name="alice",
        agent_id="sales",
        thread_id="thread",
        role_name="analyst",
        security_version=3,
    )
    assert await journal.current(**scope) == "run"
    journal_store.current_run.assert_awaited_once_with("sales", "thread")
    assert await journal.current(**{**scope, "security_version": 4}) is None
    assert await journal.current(**{**scope, "role_name": "other"}) is None
    journal_store.current_run.assert_awaited_once()
