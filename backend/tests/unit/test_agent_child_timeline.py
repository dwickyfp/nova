from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import harness_repository as repository_module
from app.modules.agents.child_timeline import activity_from_frame
from app.modules.agents.harness_repository import RUN_KEYS, HarnessRepository
from app.modules.agents.harness_worker import _table_evidence


def _frame(kind: str, payload: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"


def test_child_activity_keeps_harness_steps_without_raw_model_text() -> None:
    plan = activity_from_frame(_frame("plan", {
        "steps": [{"id": "query", "text": "Query the sales view", "status": "running"}],
        "internal": "not shown",
    }))
    tool = activity_from_frame(_frame("tool_call", {
        "tool_call_id": "call-1", "tool_name": "query_execute",
        "sql_preview": "SELECT 1", "classification": "read_only",
        "arguments": {"password": "should never persist"},
    }))

    assert plan == {"event_type": "plan", "steps": [
        {"id": "query", "text": "Query the sales view", "status": "running"}
    ]}
    assert tool is not None
    assert tool["tool_name"] == "query_execute"
    assert "arguments" not in tool
    assert activity_from_frame(_frame("text_delta", {"text": "draft"})) is None


def test_child_activity_redacts_credential_shapes_and_limits_detail() -> None:
    detail = activity_from_frame(_frame("tool_detail", {
        "tool_call_id": "call-1",
        "text": "Secret: sk-abcdefghijklmnopqrstuvwxyz123456",
    }))
    assert detail is not None
    assert detail["text"] == "[redacted]"

    progress = activity_from_frame(_frame("tool_progress", {
        "tool_call_id": "call-1", "stage": "execute",
        "text": "x" * 800, "sql_preview": "SELECT 1",
    }))
    assert progress is not None
    assert len(progress["text"]) == 400


def test_child_result_table_redacts_secret_columns_and_cells() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    result = _table_evidence({
        "columns": [secret, "api_key", "note"],
        "rows": [["label", "short-secret", "password=supersecret"]],
    })

    assert result is not None
    assert result["columns"] == ["[redacted]", "api_key", "note"]
    assert result["rows"] == [["label", "***", "[redacted]"]]
    assert secret not in json.dumps(result)


@pytest.mark.asyncio
async def test_child_mailbox_rejects_token_before_persistence(monkeypatch) -> None:
    execute = AsyncMock()
    monkeypatch.setattr(repository_module.db, "execute_system", execute)
    sender = {
        "run_id": "root", "root_run_id": None, "owner_name": "alice",
        "role_name": "analyst", "depth": 0,
        "thread_id": "chat", "session_id": "login", "security_version": 1,
    }
    recipient = {
        "run_id": "child", "root_run_id": "root", "owner_name": "alice",
        "role_name": "analyst", "depth": 1,
        "thread_id": "chat", "session_id": "login", "security_version": 1,
    }

    with pytest.raises(ValueError, match="Invalid agent message"):
        await HarnessRepository().send(
            sender=sender, recipient=recipient, operation_id="message-1",
            message_type="message", content="sk-abcdefghijklmnopqrstuvwxyz123456",
        )

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_child_event_page_includes_both_coordination_directions(monkeypatch) -> None:
    events = [
        {"event_id": 1, "run_id": "root", "type": "agent_started", "payload": {}},
        {"event_id": 2, "run_id": "root", "type": "agent_message",
         "payload": {"recipient_run_id": "child", "content": "Focus on revenue"}},
        {"event_id": 3, "run_id": "child", "type": "child_activity",
         "payload": {"event_type": "tool_call", "tool_name": "query_execute"}},
        {"event_id": 4, "run_id": "child", "type": "agent_message",
         "payload": {"recipient_run_id": "root", "content": "Found five channels"}},
        {"event_id": 5, "run_id": "root", "type": "agent_completed", "payload": {}},
    ]
    repo = HarnessRepository()

    async def page(_root: str, cursor: int, *, limit: int, ensure_complete: bool):
        assert ensure_complete is True
        return [item for item in events if item["event_id"] > cursor][:limit]

    monkeypatch.setattr(repo, "events_page", page)
    first, cursor, more = await repo.child_events_page("root", "child", -1, limit=2)
    second, final_cursor, more_after = await repo.child_events_page(
        "root", "child", cursor, limit=2
    )

    assert [item["event_id"] for item in first] == [2, 3]
    assert [item["event_id"] for item in second] == [4]
    assert cursor == 3
    assert final_cursor == 5
    assert more is True
    assert more_after is False


@pytest.mark.asyncio
async def test_completed_child_page_retries_missing_start_before_advancing_cursor(
    monkeypatch,
) -> None:
    repo = HarnessRepository()
    start = {"event_id": 1, "run_id": "child", "type": "agent_started", "payload": {}}
    done = {"event_id": 2, "run_id": "child", "type": "agent_completed", "payload": {}}
    root_done = {"event_id": 3, "run_id": "root", "type": "agent_completed", "payload": {}}
    page = AsyncMock(side_effect=[[done, root_done], [start, done, root_done]])
    monkeypatch.setattr(repo, "events_page", page)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    events, cursor, has_more = await repo.child_events_page("root", "child", -1)

    assert [event["type"] for event in events] == ["agent_started", "agent_completed"]
    assert cursor == 3
    assert has_more is False
    assert page.await_count == 2


@pytest.mark.asyncio
async def test_completed_child_timeline_retries_empty_event_page(monkeypatch) -> None:
    now = datetime.now(UTC)
    start = ["root", 10, "child", "agent_started", "{}", now]
    child_done = ["root", 11, "child", "agent_completed", "{}", now]
    root_done = ["root", 12, "root", "agent_completed", "{}", now]
    terminal_rows = [
        ["child", "agent_completed", 11],
        ["root", "agent_completed", 12],
    ]
    read = AsyncMock(side_effect=[
        ([], terminal_rows),
        ([start, child_done, root_done], terminal_rows),
    ])
    monkeypatch.setattr(HarnessRepository, "_synced_completed_event_rows", read)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    events = await HarnessRepository._events(
        "root", -1, 100,
        {"child": "agent_completed", "root": "agent_completed"},
    )

    assert [item["type"] for item in events] == [
        "agent_started", "agent_completed", "agent_completed"
    ]
    assert read.await_count == 2


@pytest.mark.asyncio
async def test_completed_child_timeline_retries_short_page_without_terminal(monkeypatch) -> None:
    now = datetime.now(UTC)
    start = ["root", 10, "child", "agent_started", "{}", now]
    child_done = ["root", 11, "child", "agent_completed", "{}", now]
    root_done = ["root", 12, "root", "agent_completed", "{}", now]
    terminal_rows = [
        ["child", "agent_completed", 11],
        ["root", "agent_completed", 12],
    ]
    read = AsyncMock(side_effect=[
        ([start, child_done], terminal_rows),
        ([start, child_done, root_done], terminal_rows),
    ])
    monkeypatch.setattr(HarnessRepository, "_synced_completed_event_rows", read)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    events = await HarnessRepository._events(
        "root", -1, 100,
        {"child": "agent_completed", "root": "agent_completed"},
    )

    assert [item["type"] for item in events] == [
        "agent_started", "agent_completed", "agent_completed"
    ]
    assert read.await_count == 2


@pytest.mark.asyncio
async def test_pending_messages_retries_wrong_shape_and_recipient(monkeypatch) -> None:
    execute = AsyncMock(side_effect=[
        {"unexpected": "stale response"},
        {"rows": [["m1", "root", "message"]]},
        {"rows": [["other", "m1", "root", "message", "Wrong", None, None, "agent"]]},
        {"rows": [["child", "m1", "root", "message", "Correct", None, None, "user"]]},
    ])
    monkeypatch.setattr(repository_module.db, "execute_system", execute)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    items = await HarnessRepository().pending_messages("child")

    assert execute.await_count == 4
    assert items == [{
        "message_id": "m1", "sender_run_id": "root", "message_type": "message",
        "content": "Correct", "correlation_id": None, "reply_to": None,
        "origin": "user",
    }]


@pytest.mark.asyncio
async def test_pending_messages_rejects_persistently_malformed_rows(monkeypatch) -> None:
    execute = AsyncMock(return_value={"rows": [["foreign", "m1", "root", "message",
                                               "Wrong", None, None, "user"]]})
    monkeypatch.setattr(repository_module.db, "execute_system", execute)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(RuntimeError, match="pending message query returned inconsistent rows"):
        await HarnessRepository().pending_messages("child")

    assert execute.await_count == 8


@pytest.mark.asyncio
async def test_thread_roots_survive_transient_empty_metadata_read(monkeypatch) -> None:
    root = dict.fromkeys(RUN_KEYS)
    root.update({
        "run_id": "root", "root_run_id": None, "agent_id": "__auto__",
        "thread_id": "thread", "owner_name": "alice", "role_name": "analyst",
        "depth": 0, "started_at": "2026-09-25 10:00:00",
        "updated_at": "2026-09-25 10:00:01", "payload": {}, "checkpoint": {},
    })
    row = [root[key] for key in RUN_KEYS]
    repo = HarnessRepository()
    reads = AsyncMock(side_effect=[[], [row], *([[row]] * 6)])
    monkeypatch.setattr(repo, "_run_rows", reads)
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    roots = await repo.roots_for_thread(
        "thread", owner_name="alice", role_name="analyst"
    )

    assert reads.await_count == 8
    assert [run["run_id"] for run in roots] == ["root"]


@pytest.mark.asyncio
async def test_thread_roots_reject_persistently_foreign_metadata(monkeypatch) -> None:
    root = dict.fromkeys(RUN_KEYS)
    root.update({
        "run_id": "root", "root_run_id": None, "agent_id": "__auto__",
        "thread_id": "foreign", "owner_name": "alice", "role_name": "analyst",
        "depth": 0, "started_at": "2026-09-25 10:00:00",
        "updated_at": "2026-09-25 10:00:01", "payload": {}, "checkpoint": {},
    })
    repo = HarnessRepository()
    monkeypatch.setattr(repo, "_run_rows", AsyncMock(
        return_value=[[root[key] for key in RUN_KEYS]]
    ))
    monkeypatch.setattr(repository_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(RuntimeError, match="thread run query returned inconsistent rows"):
        await repo.roots_for_thread("thread", owner_name="alice", role_name="analyst")
