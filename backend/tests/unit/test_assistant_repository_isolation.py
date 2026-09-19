"""Per-user isolation for persisted assistant threads (the refresh-history fix).

Threads used to live in process memory, so every reload emptied history. They are
now persisted in ``NOVA_SYSTEM``. These tests pin the property that matters most:
**one user can never see or mutate another user's history**, enforced by the
``user_name`` filter in each SQL statement — not by a post-hoc Python check.

The engine is faked with a small recording double so the tests assert the
SQL-level scoping without a live StarRocks.
"""

from __future__ import annotations

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
    assert params == ["alice"]


async def test_get_thread_filters_by_thread_and_user(recording):
    repo = AssistantRepository()
    await repo.get_thread("t1", user_name="alice")

    sql, params = recording.calls[-1]
    assert "thread_id = %s" in sql and "user_name = %s" in sql
    assert params == ["t1", "alice"]


async def test_list_messages_filters_by_thread_and_user(recording):
    repo = AssistantRepository()
    await repo.list_messages("t1", user_name="alice")

    sql, params = recording.calls[-1]
    assert "thread_id = %s" in sql and "user_name = %s" in sql
    assert params == ["t1", "alice"]


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
