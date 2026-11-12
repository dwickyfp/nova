"""Assistant ownership and projection through the PG adapter.

Real transaction, RLS, atomic append and idempotency checks also run in
integration/test_metadata_assistant_policy_bi.py. StarRocks snapshot retry
assumptions have been replaced with one consistent, actor-bound PG read.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_actor import acting_as
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied, MetadataUnavailable
from app.modules.access_control.security_context import SecurityContext
from app.modules.assistant import repository as repo_module
from app.modules.assistant.metadata_repository import AssistantMetadataRepository
from app.modules.assistant.repository import AssistantRepository, AssistantThreadListUnavailable

ALICE = SecurityContext("alice", "ANALYST", session_id="login-1")


def thread(**changes):
    return {
        "thread_id": "t1",
        "user_name": "alice",
        "title": "One",
        "workspace_file_id": None,
        "agent_id": None,
        "created_at": datetime(2026, 9, 25, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 25, tzinfo=UTC),
        "message_count": 1,
        **changes,
    }


def message(**changes):
    return {
        "message_id": "m1",
        "thread_id": "t1",
        "user_name": "alice",
        "seq": 0,
        "role": "assistant",
        "content": "There are 42 orders.",
        "agent_id": None,
        "created_at": datetime(2026, 9, 25, tzinfo=UTC),
        "model_name": "fixture",
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "steps": [{"kind": "reasoning", "text": "Checked the result"}],
        "security_context": None,
        "feedback": None,
        "attachments": [],
        "instructions": None,
        **changes,
    }


@pytest.fixture
def store(monkeypatch):
    repository = AsyncMock(spec=AssistantMetadataRepository)
    repository.list_threads.return_value = [thread()]
    repository.list_messages.return_value = [message()]
    calls = []

    @asynccontextmanager
    async def scoped(user_name=None, *, read_only=False, actor=None):
        assert user_name == "alice" and actor is None
        calls.append(read_only)
        yield repository

    monkeypatch.setattr(repo_module, "_store", scoped)
    return SimpleNamespace(repo=repository, reads=calls)


@pytest.fixture
def sql_store(monkeypatch):
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=1)))
    repository = AssistantMetadataRepository(connection, ALICE)
    monkeypatch.setattr(repository, "_bound", AsyncMock())
    monkeypatch.setattr(repository, "_one", AsyncMock(return_value=None))
    monkeypatch.setattr(repository, "_all", AsyncMock(return_value=[]))
    return repository


@pytest.mark.parametrize(
    "agent,all_agents,clause,params",
    [
        (None, False, "t.agent_id IS NULL", ("alice",)),
        ("ops", False, "t.agent_id=%s", ("alice", "ops")),
        (None, True, "t.agent_id IS NOT NULL", ("alice",)),
    ],
)
async def test_thread_query_scopes_user_and_surface(sql_store, agent, all_agents, clause, params):
    await sql_store.list_threads(agent_id=agent, all_agents=all_agents)
    sql, actual = sql_store._all.await_args.args
    assert "t.user_name=%s" in sql and clause in sql and actual == params
    assert "ORDER BY t.updated_at DESC,t.thread_id DESC" in sql


async def test_agent_and_all_agents_are_mutually_exclusive(sql_store):
    with pytest.raises(ValueError):
        await sql_store.list_threads(agent_id="ops", all_agents=True)
    sql_store._all.assert_not_awaited()


@pytest.mark.parametrize(
    "owner,agent,all_agents",
    [
        ("bob", None, False),
        ("alice", "foreign", False),
        ("alice", None, True),
    ],
)
async def test_thread_projection_rejects_out_of_scope_rows(store, owner, agent, all_agents):
    store.repo.list_threads.return_value = [thread(user_name=owner, agent_id=agent)]
    with pytest.raises(AssistantThreadListUnavailable):
        await AssistantRepository().list_threads(user_name="alice", all_agents=all_agents)
    store.repo.list_threads.assert_awaited_once()


async def test_thread_projection_rejects_duplicate_rows(store):
    store.repo.list_threads.return_value = [thread(), thread()]
    with pytest.raises(AssistantThreadListUnavailable):
        await AssistantRepository().list_threads(user_name="alice")


async def test_empty_scope_needs_one_consistent_read(store):
    store.repo.list_threads.return_value = []
    assert await AssistantRepository().list_threads(user_name="alice") == []
    store.repo.list_threads.assert_awaited_once()
    assert store.reads == [True]


async def test_outage_is_not_an_empty_thread_list(store):
    store.repo.list_threads.side_effect = MetadataUnavailable()
    with pytest.raises(MetadataUnavailable):
        await AssistantRepository().list_threads(user_name="alice")
    store.repo.list_threads.assert_awaited_once()


async def test_scope_cannot_substitute_another_principal(fake_metadata_db):
    with acting_as(ALICE), pytest.raises(MetadataPermissionDenied):
        async with repo_module._store("bob"):
            pytest.fail("wrong actor admitted")
    assert not fake_metadata_db.transactions


async def test_scope_uses_the_bound_login(fake_metadata_db):
    with acting_as(ALICE):
        async with repo_module._store("alice", read_only=True) as repository:
            assert repository.actor == ALICE
    assert fake_metadata_db.transactions[0].actor.session_id == "login-1"


async def test_get_thread_is_scoped_in_sql(sql_store):
    await sql_store.get_thread("t1")
    sql, params = sql_store._one.await_args.args
    assert "t.thread_id=%s AND t.user_name=%s" in sql
    assert params == ("t1", "alice")


async def test_create_returns_committed_row_without_polling(store):
    store.repo.create_thread.return_value = thread(agent_id="sales")
    row = await AssistantRepository().create_thread(user_name="alice", agent_id="sales")
    assert row["thread_id"] == "t1" and row["agent_id"] == "sales"
    store.repo.get_thread.assert_not_awaited()
    assert store.reads == [False]


async def test_message_query_scopes_thread_and_user(sql_store):
    await sql_store.list_messages("t1")
    sql, params = sql_store._all.await_args.args
    assert "thread_id=%s AND user_name=%s ORDER BY seq,message_id" in sql
    assert params == ("t1", "alice")


async def test_message_replay_preserves_trace_and_usage(store):
    rows = await AssistantRepository().list_messages("t1", user_name="alice")
    assert rows[0]["steps"] == message()["steps"]
    assert rows[0]["total_tokens"] == 150 and rows[0]["model_name"] == "fixture"
    assert store.reads == [True]


@pytest.mark.parametrize(
    "steps,expected",
    [
        ('[{"kind":"answer"}]', [{"kind": "answer"}]),
        ("{not json", []),
        (None, []),
    ],
)
async def test_message_replay_decodes_trace_without_losing_the_answer(store, steps, expected):
    store.repo.list_messages.return_value = [message(steps=steps)]
    rows = await AssistantRepository().list_messages("t1", user_name="alice")
    assert rows[0]["steps"] == expected and rows[0]["content"] == message()["content"]


@pytest.mark.parametrize(
    "changes",
    [
        {"user_name": "bob"},
        {"thread_id": "foreign"},
        {"role": "alice"},
        {"seq": -1},
        {"seq": True},
        {"message_id": ""},
    ],
)
async def test_message_replay_refuses_invalid_or_foreign_rows(store, changes):
    store.repo.list_messages.return_value = [message(**changes)]
    with pytest.raises(AssistantThreadListUnavailable):
        await AssistantRepository().list_messages("t1", user_name="alice")


async def test_delete_scopes_every_owned_table(sql_store):
    await sql_store.delete_thread("t1")
    for call in sql_store.connection.execute.await_args_list:
        sql, params = call.args
        assert "thread_id=%s AND user_name=%s" in sql
        assert params == ("t1", "alice")


async def test_append_locks_the_owned_thread_before_allocating_sequence(sql_store):
    sql_store._one.side_effect = [None, {"thread_id": "t1"}, {"seq": 4}, message(seq=4)]
    saved, created = await sql_store.append_message(
        "t1", role="user", content="hi", message_id="m1"
    )
    lock, sequence, insert = sql_store._one.await_args_list[1:]
    assert "thread_id=%s AND user_name=%s FOR UPDATE" in lock.args[0]
    assert lock.args[1] == ("t1", "alice")
    assert "max(seq)+1" in sequence.args[0]
    assert insert.args[1][:3] == ("m1", "alice", 4)
    assert created and saved["seq"] == 4


async def test_append_cannot_insert_into_an_absent_or_foreign_thread(sql_store):
    sql_store._one.side_effect = [None, None]
    with pytest.raises(MetadataConflict):
        await sql_store.append_message("foreign", role="user", content="hi")
    assert all("INSERT" not in call.args[0] for call in sql_store._one.await_args_list)


async def test_runtime_does_not_install_legacy_schema():
    await AssistantRepository().ensure_schema()
    assert not hasattr(repo_module, "THREADS_DDL")
    assert not hasattr(repo_module, "MESSAGES_DDL")


async def test_thread_list_routes_return_503_for_persistent_unavailability(monkeypatch):
    from fastapi import HTTPException

    from app.modules.agents import router as agents_router
    from app.modules.assistant import router as assistant_router

    failure = AssistantThreadListUnavailable("temporary")
    monkeypatch.setattr(
        assistant_router.assistant_repository,
        "list_threads",
        AsyncMock(side_effect=failure),
    )
    monkeypatch.setattr(agents_router, "_require_agent", AsyncMock())

    with pytest.raises(HTTPException) as assistant_error:
        await assistant_router.list_threads(user={"username": "alice"})
    with pytest.raises(HTTPException) as agent_error:
        await agents_router.list_agent_threads("ops", user={"username": "alice"})

    assert assistant_error.value.status_code == agent_error.value.status_code == 503
