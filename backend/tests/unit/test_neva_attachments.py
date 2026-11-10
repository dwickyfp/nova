"""A file attached in Neva's panel reaches the turn as data, is kept and audited."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.modules.assistant.schemas import MessageRequest

USER = {
    "username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"],
    "session_id": "session", "security_context_version": 1, "encrypted_password": "encrypted",
}
#: Text files travel as text, as the Studio composer sends them.
CSV = "city,orders\nJakarta,12\n"


def test_a_message_may_be_only_a_file_but_not_nothing():
    request = MessageRequest(content="", attachments=[
        {"name": "orders.csv", "content": CSV, "media_type": "text/plain"},
    ])
    assert request.prepared_attachments[0]["name"] == "orders.csv"
    with pytest.raises(ValidationError):
        MessageRequest(content="  ")
    with pytest.raises(ValidationError):
        MessageRequest(content="x", attachments=[
            {"name": f"f{index}.txt", "content": CSV} for index in range(4)
        ])


async def test_the_file_is_read_as_data_kept_and_audited(monkeypatch):
    from app.modules.assistant import router as neva
    from app.modules.assistant.state import thread_store

    seen: dict = {}

    class Loop:
        async def run(self, *, thread, user_content, **_kwargs):
            seen["content"] = user_content
            seen["last"] = thread.messages[-1]
            yield 'event: text_delta\ndata: {"text": "Jakarta has 12 orders."}\n\n'
            yield 'event: done\ndata: {"message_id": "reply", "finish_reason": "stop"}\n\n'

    append = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(neva, "_require_thread", AsyncMock(return_value={
        "title": "Files", "thread_id": "neva-files",
    }))
    monkeypatch.setattr(neva.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(neva.assistant_repository, "append_message", append)
    monkeypatch.setattr(neva, "write_audit_log", audit)
    monkeypatch.setattr(neva, "_neva_loop", lambda _memory="": Loop())
    monkeypatch.setattr(neva, "_readable_views", AsyncMock(return_value=[]))
    monkeypatch.setattr(neva.neva_memory, "memory_note", AsyncMock(return_value=""))
    monkeypatch.setattr(neva.neva_memory, "learn", AsyncMock(return_value=0))
    body = MessageRequest(content="How many orders?", attachments=[
        {"name": "orders.csv", "content": CSV, "media_type": "text/plain"},
    ])
    try:
        response = await neva.send_message(
            "neva-files", body, SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
            USER,
        )
        frames = [frame async for frame in response.body_iterator]
    finally:
        thread_store.remove("neva-files", user_name=USER["username"])

    assert any("Jakarta has 12 orders." in frame for frame in frames)
    # The model reads the file, framed as data and not as instructions.
    assert "Jakarta,12" in seen["content"] and "not instructions" in seen["content"]
    assert seen["last"].attachments[0]["name"] == "orders.csv"
    # The stored question is the person's own words; the file is kept beside it.
    stored = append.await_args_list[0].kwargs
    assert stored["content"] == "How many orders?"
    assert stored["attachments"][0]["name"] == "orders.csv"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["event_type"] == "ASSISTANT_ATTACHMENT"
    assert audit.await_args.kwargs["active_role"] == "analyst"
