"""Per-user isolation for persisted assistant threads (the refresh-history fix).

Threads used to live in process memory, so every reload emptied history. They are
now persisted in ``NOVA_SYSTEM``. These tests pin the property that matters most:
**one user can never see or mutate another user's history**, enforced by the
``user_name`` filter in each SQL statement — not by a post-hoc Python check.

The engine is faked with a small recording double so the tests assert the
SQL-level scoping without a live StarRocks.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant import repository as repo_module
from app.modules.assistant.repository import (
    AssistantRepository,
    AssistantThreadListUnavailable,
)


class _RecordingDB:
    """Stand-in for ``db`` that records every statement and its params.

    Only the shapes the repository issues are simulated: a SELECT returns rows,
    an INSERT/DELETE returns an affected count.
    """

    def __init__(self, select_rows: list[list] | None = None) -> None:
        self.calls: list[tuple[str, list | None]] = []
        self._select_rows = select_rows or []

    async def execute_system(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        normalized = " ".join(sql.split()).upper()
        if "AS REQUESTED_USER" in normalized:
            return {"columns": [], "rows": [[params[0], params[1], len(self._select_rows)]]}
        if normalized.startswith("SELECT"):
            return {"columns": [], "rows": self._select_rows, "row_count": len(self._select_rows)}
        return {"columns": [], "rows": [], "affected": 1}


@pytest.fixture
def recording(monkeypatch):
    db = _RecordingDB()
    monkeypatch.setattr(repo_module, "db", db)
    return db


async def test_list_threads_filters_by_user_in_sql(recording):
    repo = AssistantRepository()
    recording._select_rows = [[
        "thread", "alice", "Title", None, None,
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 0,
    ]]
    await repo.list_threads(user_name="alice")

    sql, params = recording.calls[-2]
    assert "user_name = %s" in sql
    assert "t.agent_id IS NULL" in sql
    assert params == ["alice"]
    count_sql, count_params = recording.calls[-1]
    assert "COUNT(*)" in count_sql
    assert "t.user_name = %s" in count_sql
    assert "t.agent_id IS NULL" in count_sql
    assert count_params == ["alice", None, "alice"]


async def test_studio_threads_are_scoped_to_the_requested_agent(recording):
    recording._select_rows = [[
        "thread", "alice", "Title", None, "agent-1",
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 0,
    ]]
    await AssistantRepository().list_threads(user_name="alice", agent_id="agent-1")

    sql, params = recording.calls[-2]
    assert "t.agent_id = %s" in sql
    assert "t.agent_id IS NULL" not in sql
    assert params == ["alice", "agent-1"]
    count_sql, count_params = recording.calls[-1]
    assert "t.user_name = %s" in count_sql
    assert "t.agent_id = %s" in count_sql
    assert count_params == ["alice", "agent-1", "alice", "agent-1"]


@pytest.mark.parametrize(
    "bad_rows",
    [
        [["thread", "alice", "Short"]],
        [[
            "thread", "mallory", "Title", None, "agent-1",
            "2026-09-25 00:00:00", "2026-09-25 00:00:00", 0,
        ]],
        [[
            "thread", "alice", "Title", None, "agent-2",
            "2026-09-25 00:00:00", "2026-09-25 00:00:00", 0,
        ]],
    ],
)
async def test_list_threads_retries_malformed_or_out_of_scope_rows(
    monkeypatch, bad_rows
):
    valid = [
        "thread", "alice", "Title", None, "agent-1",
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 2,
    ]
    execute = AsyncMock(side_effect=[
        {"rows": bad_rows},
        {"rows": [valid]},
        {"rows": [["alice", "agent-1", 1]]},
    ])
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    threads = await AssistantRepository().list_threads(
        user_name="alice", agent_id="agent-1"
    )

    assert execute.await_count == 3
    assert threads[0]["thread_id"] == "thread"
    assert threads[0]["message_count"] == 2
    assert execute.await_args_list[0].args[1] == ["alice", "agent-1"]
    assert execute.await_args_list[1].args[1] == ["alice", "agent-1"]
    assert execute.await_args_list[2].args[1] == [
        "alice", "agent-1", "alice", "agent-1",
    ]


async def test_list_threads_reports_persistent_malformed_read_as_unavailable(monkeypatch):
    execute = AsyncMock(return_value={"rows": [["thread", "alice"]]})
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(AssistantThreadListUnavailable):
        await AssistantRepository().list_threads(user_name="alice", agent_id=None)

    assert execute.await_count == 8


async def test_list_threads_retries_transient_empty_snapshot(monkeypatch):
    valid = [
        "thread", "alice", "Title", None, "__auto__",
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 1,
    ]
    execute = AsyncMock(side_effect=[
        {"rows": []},
        {"rows": [["alice", "__auto__", 11]]},
        {"rows": [valid]},
        {"rows": [["alice", "__auto__", 1]]},
    ])
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    threads = await AssistantRepository().list_threads(
        user_name="alice", agent_id="__auto__"
    )

    assert len(threads) == 1
    assert execute.await_count == 4
    assert execute.await_args_list[1].args[1] == [
        "alice", "__auto__", "alice", "__auto__",
    ]


async def test_list_threads_confirms_genuine_empty_scope(monkeypatch):
    execute = AsyncMock(side_effect=[
        {"rows": []}, {"rows": [["alice", None, 0]]},
        {"rows": []}, {"rows": [["alice", None, 0]]},
        {"rows": []}, {"rows": [["alice", None, 0]]},
    ])
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    assert await AssistantRepository().list_threads(user_name="alice") == []
    assert execute.await_count == 6


async def test_list_threads_retries_partial_nonempty_snapshot(monkeypatch):
    first = [
        "t1", "alice", "One", None, "__auto__",
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 1,
    ]
    second = [
        "t2", "alice", "Two", None, "__auto__",
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 0,
    ]
    execute = AsyncMock(side_effect=[
        {"rows": [first]}, {"rows": [["alice", "__auto__", 2]]},
        {"rows": [first, second]}, {"rows": [["alice", "__auto__", 2]]},
    ])
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    threads = await AssistantRepository().list_threads(
        user_name="alice", agent_id="__auto__"
    )

    assert [thread["thread_id"] for thread in threads] == ["t1", "t2"]
    assert execute.await_count == 4
    assert all(
        call.args[1] == ["alice", "__auto__"]
        for call in (execute.await_args_list[0], execute.await_args_list[2])
    )
    assert all(
        call.args[1] == ["alice", "__auto__", "alice", "__auto__"]
        for call in (execute.await_args_list[1], execute.await_args_list[3])
    )


async def test_list_threads_rejects_persistent_partial_nonempty_snapshot(monkeypatch):
    partial = [
        "t1", "alice", "One", None, "__auto__",
        "2026-09-25 00:00:00", "2026-09-25 00:00:00", 1,
    ]
    calls = 0

    async def partial_snapshot(sql, params):
        nonlocal calls
        calls += 1
        if "AS requested_user" in sql:
            return {"rows": [["alice", "__auto__", 2]]}
        return {"rows": [partial]}

    monkeypatch.setattr(repo_module.db, "execute_system", partial_snapshot)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(AssistantThreadListUnavailable, match="temporarily unavailable"):
        await AssistantRepository().list_threads(
            user_name="alice", agent_id="__auto__"
        )

    assert calls == 16


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
        await agents_router.list_agent_threads(
            "__auto__", user={"username": "alice"}
        )

    assert assistant_error.value.status_code == agent_error.value.status_code == 503


async def test_get_thread_filters_by_thread_and_user(recording):
    repo = AssistantRepository()
    await repo.get_thread("t1", user_name="alice")

    sql, params = recording.calls[-1]
    assert "thread_id = %s" in sql and "user_name = %s" in sql
    assert params == ["t1", "alice"]


async def test_new_thread_is_readable_before_create_returns(recording, monkeypatch):
    repo = AssistantRepository()
    read = AsyncMock(side_effect=[None, {"thread_id": "visible"}])
    monkeypatch.setattr(repo, "get_thread", read)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    created = await repo.create_thread(user_name="alice", agent_id="sales")

    assert read.await_count == 2
    assert all(call.kwargs["user_name"] == "alice" for call in read.await_args_list)
    assert created["agent_id"] == "sales"


async def test_list_messages_filters_by_thread_and_user(recording):
    repo = AssistantRepository()
    await repo.list_messages("t1", user_name="alice")

    sql, params = recording.calls[-1]
    assert "thread_id = %s" in sql and "user_name = %s" in sql
    assert params == ["t1", "alice"]


async def test_synced_message_read_uses_one_connection_for_sync_and_select(monkeypatch):
    calls: list[tuple[str, list | None]] = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, sql, params=None):
            calls.append((" ".join(sql.split()), params))

        async def fetchall(self):
            return [("final", "assistant", "Answer", "2026-09-25 00:00:00")]

    class Connection:
        def cursor(self):
            return Cursor()

    @asynccontextmanager
    async def system_conn():
        yield Connection()

    monkeypatch.setattr(repo_module.db, "system_conn", system_conn)

    messages = await AssistantRepository().list_messages(
        "thread", user_name="alice", synchronize=True
    )

    assert messages[0]["message_id"] == "final"
    assert calls[0] == ("SYNC", None)
    assert "WHERE thread_id = %s AND user_name = %s" in calls[1][0]
    assert calls[1][1] == ["thread", "alice"]


async def test_list_messages_returns_the_trace_and_usage(monkeypatch):
    """A reopened thread must carry the turn's steps and token counts.

    ``list_messages`` used to select text only, which is why a reload showed the
    answers and silently dropped the process that produced them.
    """
    step = {"kind": "reasoning", "phase": "act", "text": "Reasoning"}
    db = _RecordingDB(
        select_rows=[
            [
                "m1",
                "assistant",
                "There are 42 orders.",
                "2026-01-01 00:00:00",
                "gpt-4o",
                120,
                30,
                150,
                [step],
            ]
        ]
    )
    monkeypatch.setattr(repo_module, "db", db)

    rows = await AssistantRepository().list_messages("t1", user_name="alice")

    assert rows[0]["steps"] == [step]
    assert rows[0]["total_tokens"] == 150
    assert rows[0]["model_name"] == "gpt-4o"


async def test_list_messages_tolerates_a_string_steps_column(monkeypatch):
    """The driver may hand back a JSON column as text; it must still decode."""
    db = _RecordingDB(
        select_rows=[
            [
                "m1",
                "assistant",
                "ok",
                "2026-01-01 00:00:00",
                None,
                None,
                None,
                None,
                '[{"kind":"answer"}]',
            ]
        ]
    )
    monkeypatch.setattr(repo_module, "db", db)

    rows = await AssistantRepository().list_messages("t1", user_name="alice")

    assert rows[0]["steps"] == [{"kind": "answer"}]


async def test_list_messages_tolerates_a_malformed_steps_column(monkeypatch):
    """A conversation must open even if its stored trace is unreadable."""
    db = _RecordingDB(
        select_rows=[
            [
                "m1",
                "assistant",
                "ok",
                "2026-01-01 00:00:00",
                None,
                None,
                None,
                None,
                "{not json",
            ]
        ]
    )
    monkeypatch.setattr(repo_module, "db", db)

    rows = await AssistantRepository().list_messages("t1", user_name="alice")

    assert rows[0]["steps"] == []


async def test_list_messages_retries_inconsistent_starrocks_row(monkeypatch):
    """A transient row from another query must not leak into thread replay."""
    execute = AsyncMock(
        side_effect=[
            {"rows": [["m1", "alice", "bad", "2026-01-01", None, None, None, None, []]]},
            {"rows": [["m2", "assistant", "Recovered", "2026-01-01", None, 1, 2, 3, []]]},
        ]
    )
    monkeypatch.setattr(repo_module.db, "execute_system", execute)

    rows = await AssistantRepository().list_messages("t1", user_name="alice")

    assert execute.await_count == 2
    assert rows[0]["content"] == "Recovered"


async def test_delete_thread_scopes_both_tables_to_the_owner(recording):
    repo = AssistantRepository()
    await repo.delete_thread("t1", user_name="alice")

    message_delete, thread_delete = recording.calls[-2], recording.calls[-1]
    assert "CONFIG_ASSISTANT_MESSAGES" in message_delete[0]
    assert message_delete[1] == ["t1", "alice"]
    assert "CONFIG_ASSISTANT_THREADS" in thread_delete[0]
    assert thread_delete[1] == ["t1", "alice"]


async def test_append_message_scopes_the_count_and_insert(recording):
    # The COUNT that computes ``seq`` is user-scoped too, so a foreign thread id
    # can never influence another user's message ordering.
    repo = AssistantRepository()
    recording._select_rows = [["t1", "alice", 0]]
    await repo.append_message("t1", user_name="alice", role="user", content="hi")

    count_call = next(c for c in recording.calls if c[0].upper().startswith("SELECT"))
    assert count_call[1] == ["t1", "alice", "t1", "alice"]
    insert_call = next(c for c in recording.calls if "INSERT INTO" in c[0])
    assert insert_call[1][1] == "t1"
    assert insert_call[1][2] == "alice"


async def test_append_message_retries_mismatched_count_scope(monkeypatch):
    execute = AsyncMock(side_effect=[
        {"unexpected": "stale response"},
        {"rows": [["foreign", "alice", 9]]},
        {"rows": [["t1", "alice", "1"]]},
        {"rows": [], "affected": 1},
        {"rows": [], "affected": 1},
    ])
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    result = await AssistantRepository().append_message(
        "t1", user_name="alice", role="assistant", content="Recovered"
    )

    assert execute.await_count == 5
    assert execute.await_args_list[3].args[1][3] == 1
    assert result["content"] == "Recovered"


async def test_append_message_rejects_persistently_malformed_count(monkeypatch):
    execute = AsyncMock(return_value={"rows": [["unrelated result"]]})
    monkeypatch.setattr(repo_module.db, "execute_system", execute)
    monkeypatch.setattr(repo_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(RuntimeError, match="count query returned inconsistent rows"):
        await AssistantRepository().append_message(
            "t1", user_name="alice", role="assistant", content="Do not insert"
        )

    assert execute.await_count == 8


def test_ddl_creates_both_tables_with_the_nova_system_schema():
    assert "NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS" in repo_module.THREADS_DDL
    assert "NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES" in repo_module.MESSAGES_DDL
    # Primary Key tables, per the CRUD rule.
    assert "PRIMARY KEY" in repo_module.THREADS_DDL
    assert "PRIMARY KEY" in repo_module.MESSAGES_DDL
