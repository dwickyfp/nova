"""Per-user isolation for persisted assistant threads (the refresh-history fix).

Threads used to live in process memory, so every reload emptied history. They are
now persisted in ``NOVA_SYSTEM``. These tests pin the property that matters most:
**one user can never see or mutate another user's history**, enforced by the
``user_name`` filter in each SQL statement — not by a post-hoc Python check.

The engine is faked with a small recording double so the tests assert the
SQL-level scoping without a live StarRocks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.assistant import repository as repo_module
from app.modules.assistant.repository import AssistantRepository


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
    await repo.list_threads(user_name="alice")

    sql, params = recording.calls[-1]
    assert "user_name = %s" in sql
    assert "t.agent_id IS NULL" in sql
    assert params == ["alice"]


async def test_studio_threads_are_scoped_to_the_requested_agent(recording):
    await AssistantRepository().list_threads(user_name="alice", agent_id="agent-1")

    sql, params = recording.calls[-1]
    assert "t.agent_id = %s" in sql
    assert "t.agent_id IS NULL" not in sql
    assert params == ["alice", "agent-1"]


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
    await repo.append_message("t1", user_name="alice", role="user", content="hi")

    count_call = next(c for c in recording.calls if c[0].upper().startswith("SELECT"))
    assert count_call[1] == ["t1", "alice"]
    insert_call = next(c for c in recording.calls if "INSERT INTO" in c[0])
    assert insert_call[1][1] == "t1"
    assert insert_call[1][2] == "alice"


def test_ddl_creates_both_tables_with_the_nova_system_schema():
    assert "NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS" in repo_module.THREADS_DDL
    assert "NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES" in repo_module.MESSAGES_DDL
    # Primary Key tables, per the CRUD rule.
    assert "PRIMARY KEY" in repo_module.THREADS_DDL
    assert "PRIMARY KEY" in repo_module.MESSAGES_DDL
