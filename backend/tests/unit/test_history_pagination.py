from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataUnavailable
from app.modules.assistant import history
from app.modules.assistant.repository import AssistantThreadListUnavailable


def thread(row_id: str, user: str = "alice", agent: str | None = None) -> dict:
    return {
        "thread_id": row_id, "user_name": user, "title": row_id, "workspace_file_id": None,
        "agent_id": agent, "created_at": datetime(2026, 9, 25, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 25, tzinfo=UTC), "message_count": 0,
    }


def message(row_id: str, seq: int) -> dict:
    return {
        "message_id": row_id, "role": "assistant", "content": "answer",
        "created_at": datetime(2026, 9, 25, tzinfo=UTC), "model_name": None,
        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3,
        "steps": [{"type": "text_delta", "text": "answer"}], "security_context": None,
        "feedback": None, "attachments": [], "seq": seq,
    }


@pytest.fixture
def store(monkeypatch):
    repo = AsyncMock()

    @asynccontextmanager
    async def scoped(user_name, *, read_only):
        assert user_name == "alice" and read_only is True
        yield repo

    monkeypatch.setattr(history, "_store", scoped)
    return repo


async def test_thread_pages_count_only_visible_threads_and_use_tie_breaker(store):
    store.thread_page.side_effect = [
        [{**thread("c"), "message_count": 4}, {**thread("b"), "message_count": 2}, thread("a")],
        [thread("a")],
    ]
    repo = history.HistoryRepository()
    first, cursor = await repo.threads(user_name="alice", limit=2)
    second, end = await repo.threads(user_name="alice", limit=2, cursor=cursor)
    assert [row["thread_id"] for row in first + second] == ["c", "b", "a"]
    assert [row["message_count"] for row in first + second] == [4, 2, 0]
    assert cursor and end is None
    assert store.thread_page.await_args_list[1].kwargs == {
        "limit": 3, "agent_ids": None, "all_agents": False,
        "after": (datetime(2026, 9, 25, tzinfo=UTC), "b"),
    }


async def test_empty_page_needs_one_query_without_recount_or_retry(store):
    store.thread_page.return_value = []
    assert await history.HistoryRepository().threads(user_name="alice") == ([], None)
    store.thread_page.assert_awaited_once()


@pytest.mark.parametrize("bad", [thread("a", "bob"), thread("a", "alice", "foreign")])
async def test_thread_page_fails_closed_on_wrong_scope(store, bad):
    store.thread_page.return_value = [bad]
    with pytest.raises(AssistantThreadListUnavailable):
        await history.HistoryRepository().threads(user_name="alice")


async def test_cursor_cannot_cross_user_or_surface(store):
    store.thread_page.return_value = [thread("b"), thread("a")]
    repo = history.HistoryRepository()
    _, cursor = await repo.threads(user_name="alice", limit=1)
    for scope in [{"user_name": "bob"}, {"user_name": "alice", "all_agents": True}]:
        with pytest.raises(ValueError, match="cursor"):
            await repo.threads(**scope, cursor=cursor)
    store.thread_page.assert_awaited_once()


async def test_message_pages_preserve_traces_and_equal_sequence_rows(store):
    store.message_page.side_effect = [
        [message("c", 2), message("b", 1), message("a", 1)], [message("a", 1)],
    ]
    repo = history.HistoryRepository()
    newest, cursor = await repo.messages("thread", user_name="alice", limit=2)
    older, end = await repo.messages("thread", user_name="alice", limit=2, cursor=cursor)
    assert [row["message_id"] for row in older + newest] == ["a", "b", "c"]
    assert newest[0]["steps"][0]["text"] == "answer"
    assert end is None
    store.message_page.assert_awaited_with("thread", limit=3, before=(1, "b"))
    with pytest.raises(ValueError):
        await repo.messages("other", user_name="alice", cursor=cursor)


@pytest.mark.parametrize("cursor", ["not-base64", "W10", "e30", "bnVsbA", "x" * 1025])
async def test_invalid_cursor_never_reaches_database(store, cursor):
    with pytest.raises(ValueError):
        await history.HistoryRepository().threads(user_name="alice", cursor=cursor)
    store.thread_page.assert_not_awaited()


@pytest.mark.parametrize("rows", [[message("a", -1)], [message("a", 1), message("a", 1)]])
async def test_invalid_message_page_fails_closed(store, rows):
    store.message_page.return_value = rows
    with pytest.raises(AssistantThreadListUnavailable):
        await history.HistoryRepository().messages("thread", user_name="alice")


async def test_database_failure_returns_history_unavailable(store):
    store.message_page.side_effect = MetadataUnavailable()
    with pytest.raises(MetadataUnavailable, match="unavailable"):
        await history.HistoryRepository().messages("thread", user_name="alice")
