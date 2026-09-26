from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant import history
from app.modules.assistant.repository import AssistantThreadListUnavailable


def thread(row_id: str, user: str = "alice", agent: str | None = None) -> list:
    return [row_id, user, row_id, None, agent, datetime(2026, 9, 25), datetime(2026, 9, 25), 0]


def message(row_id: str, seq: int) -> list:
    return [
        row_id,
        "assistant",
        "answer",
        datetime(2026, 9, 25),
        None,
        1,
        2,
        3,
        '[{"type":"text_delta","text":"answer"}]',
        None,
        None,
        "[]",
        seq,
    ]


async def test_thread_pages_count_only_visible_threads_and_use_tie_breaker(monkeypatch):
    execute = AsyncMock(
        side_effect=[
            {"rows": [thread("c"), thread("b"), thread("a")]},
            {"rows": [["c", 4], ["b", 2]]},
            {"rows": [thread("a")]},
            {"rows": []},
        ]
    )
    monkeypatch.setattr(history.db, "execute_system", execute)
    repo = history.HistoryRepository()
    first, cursor = await repo.threads(user_name="alice", limit=2)
    second, end = await repo.threads(user_name="alice", limit=2, cursor=cursor)
    assert [row["thread_id"] for row in first + second] == ["c", "b", "a"]
    assert [row["message_count"] for row in first + second] == [4, 2, 0]
    assert cursor and end is None
    sql, params = execute.await_args_list[1].args
    assert "user_name = %s AND thread_id IN (%s,%s)" in sql
    assert params == ["alice", "c", "b"]
    sql, params = execute.await_args_list[2].args
    assert "t.updated_at < %s OR (t.updated_at = %s AND t.thread_id < %s)" in sql
    assert "OFFSET" not in sql
    assert params[-2:] == ["b", 3]


async def test_empty_page_needs_one_query_without_recount_or_retry(monkeypatch):
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(history.db, "execute_system", execute)
    assert await history.HistoryRepository().threads(user_name="alice") == ([], None)
    assert execute.await_count == 1


@pytest.mark.parametrize("bad", [thread("a", "bob"), thread("a", "alice", "foreign")])
async def test_thread_page_fails_closed_on_wrong_scope(monkeypatch, bad):
    monkeypatch.setattr(history.db, "execute_system", AsyncMock(return_value={"rows": [bad]}))
    with pytest.raises(AssistantThreadListUnavailable):
        await history.HistoryRepository().threads(user_name="alice")


async def test_cursor_cannot_cross_user_or_surface(monkeypatch):
    execute = AsyncMock(side_effect=[{"rows": [thread("b"), thread("a")]}, {"rows": []}])
    monkeypatch.setattr(history.db, "execute_system", execute)
    repo = history.HistoryRepository()
    _, cursor = await repo.threads(user_name="alice", limit=1)
    for scope in [{"user_name": "bob"}, {"user_name": "alice", "all_agents": True}]:
        with pytest.raises(ValueError, match="cursor"):
            await repo.threads(**scope, cursor=cursor)
    assert execute.await_count == 2


async def test_message_pages_preserve_traces_and_equal_sequence_rows(monkeypatch):
    execute = AsyncMock(
        side_effect=[
            {"rows": [message("c", 2), message("b", 1), message("a", 1)]},
            {"rows": [message("a", 1)]},
        ]
    )
    monkeypatch.setattr(history.db, "execute_system", execute)
    repo = history.HistoryRepository()
    newest, cursor = await repo.messages("thread", user_name="alice", limit=2)
    older, end = await repo.messages("thread", user_name="alice", limit=2, cursor=cursor)
    assert [row["message_id"] for row in older + newest] == ["a", "b", "c"]
    assert newest[0]["steps"][0]["text"] == "answer"
    assert end is None
    sql, params = execute.await_args_list[1].args
    assert "thread_id = %s AND user_name = %s" in sql
    assert "seq < %s OR (seq = %s AND message_id < %s)" in sql
    assert params == ["thread", "alice", 1, 1, "b", 3]
    with pytest.raises(ValueError):
        await repo.messages("other", user_name="alice", cursor=cursor)


@pytest.mark.parametrize("cursor", ["not-base64", "W10", "e30", "bnVsbA", "x" * 1025])
async def test_invalid_cursor_never_reaches_database(monkeypatch, cursor):
    execute = AsyncMock()
    monkeypatch.setattr(history.db, "execute_system", execute)
    with pytest.raises(ValueError):
        await history.HistoryRepository().threads(user_name="alice", cursor=cursor)
    execute.assert_not_awaited()


@pytest.mark.parametrize("rows", [[message("a", -1)], [message("a", 1), message("a", 1)]])
async def test_invalid_message_page_fails_closed(monkeypatch, rows):
    monkeypatch.setattr(history.db, "execute_system", AsyncMock(return_value={"rows": rows}))
    with pytest.raises(AssistantThreadListUnavailable):
        await history.HistoryRepository().messages("thread", user_name="alice")


async def test_database_failure_returns_history_unavailable(monkeypatch):
    monkeypatch.setattr(
        history.db, "execute_system", AsyncMock(side_effect=RuntimeError("internal"))
    )
    with pytest.raises(AssistantThreadListUnavailable, match="temporarily unavailable"):
        await history.HistoryRepository().messages("thread", user_name="alice")
