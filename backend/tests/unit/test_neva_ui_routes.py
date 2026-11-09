"""Neva's interactive answers use the same checks as Studio's, on Neva threads."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.assistant import intelligent_ui as ui
from app.modules.assistant import router as neva
from app.modules.assistant import ui_access
from app.modules.assistant.schemas import MessageRequest, UiActionInput, UiStateInput
from tests.unit.test_intelligent_ui import CALCULATOR

USER = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"],
        "session_id": "device", "security_context_version": 1, "encrypted_password": "x"}


@pytest.fixture
async def block(monkeypatch):
    stored = await ui.compose_block(CALCULATOR, "Adjust assumptions.", tables={}, business=False,
                                    question="Calculator", claims=None, language="en")
    monkeypatch.setattr(neva, "_require_thread", AsyncMock(return_value={"title": "T"}))
    monkeypatch.setattr(neva.assistant_repository, "ui_source", AsyncMock(return_value=stored))
    monkeypatch.setattr(ui_access, "write_audit_log", AsyncMock())
    return stored


async def test_a_foreign_thread_is_refused_before_any_block_is_read(monkeypatch, block):
    monkeypatch.setattr(neva, "_require_thread", AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Thread not found")))
    source = AsyncMock()
    monkeypatch.setattr(neva.assistant_repository, "ui_source", source)
    with pytest.raises(HTTPException) as refusal:
        await neva.get_ui_state("someone-else", "m", "content-1", USER)
    assert refusal.value.status_code == 404
    source.assert_not_awaited()


async def test_state_is_read_and_written_under_the_active_role(monkeypatch, block):
    get = AsyncMock(return_value={"revision": 0, "state": {"$units": 5}})
    put = AsyncMock(return_value={"revision": 1, "state": {"$units": 6}})
    monkeypatch.setattr(neva.assistant_repository, "get_ui_state", get)
    monkeypatch.setattr(neva.assistant_repository, "put_ui_state", put)
    assert (await neva.get_ui_state("t", "m", "content-1", USER))["state"] == {"$units": 5}
    assert get.await_args.kwargs["security_context"]["active_role"] == "analyst"
    await neva.put_ui_state("t", "m", "content-1", UiStateInput(
        revision=0, program_sha256=block["program_sha256"], state={"$units": 6},
    ), USER)
    assert put.await_args.kwargs["security_context"]["active_role"] == "analyst"
    with pytest.raises(HTTPException) as changed:
        await neva.put_ui_state("t", "m", "content-1", UiStateInput(
            revision=0, program_sha256="0" * 64, state={"$units": 6},
        ), USER)
    assert changed.value.status_code == 409


async def test_a_block_from_another_security_context_is_unavailable(monkeypatch, block):
    monkeypatch.setattr(neva.assistant_repository, "ui_source", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as refusal:
        await neva.get_ui_state("t", "m", "content-1", USER)
    assert refusal.value.status_code == 404
    assert ui_access.write_audit_log.await_args.kwargs["status"] == "DENIED"


@pytest.mark.parametrize("seen", [False, True])
async def test_an_action_is_accepted_once_as_user_input(monkeypatch, block, seen):
    from app.core.redis import session_store

    @asynccontextmanager
    async def lock(_key):
        yield

    monkeypatch.setattr("app.modules.intelligence.engine_repository.metadata_lock", lock)
    monkeypatch.setattr(session_store, "_redis", SimpleNamespace(set=AsyncMock(return_value=True)))
    monkeypatch.setattr(neva.assistant_repository, "ui_action_seen", AsyncMock(return_value=seen))
    action = UiActionInput(
        message_id="m", content_id="content-1", program_sha256=block["program_sha256"],
        action_id="continue", operation_id="00000000-0000-0000-0000-000000000000",
        state={"$units": 8},
    )
    if seen:
        with pytest.raises(HTTPException) as refusal:
            await ui_access.accept_action("t", action, "Continue", USER)
        assert refusal.value.status_code == 409
        return
    text, step = await ui_access.accept_action("t", action, "Continue", USER)
    assert text.startswith("Continue\n\nUI form input (user supplied):")
    assert json.loads(text.split(":\n", 1)[1])["action_id"] == "continue"
    assert step["kind"] == "ui_action"


async def test_the_reply_is_stored_under_the_done_id_before_done_is_sent(monkeypatch):
    from app.modules.assistant.state import thread_store

    order: list[str] = []

    class Loop:
        async def run(self, **_kwargs):
            yield 'event: text_delta\ndata: {"text": "Here it is."}\n\n'
            yield 'event: done\ndata: {"message_id": "reply-1", "finish_reason": "stop"}\n\n'

    async def append(*_args, **kwargs):
        order.append(f"stored:{kwargs.get('role')}:{kwargs.get('message_id')}")

    monkeypatch.setattr(neva, "_require_thread", AsyncMock(return_value={"title": "T"}))
    monkeypatch.setattr(neva.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(neva.assistant_repository, "append_message", append)
    monkeypatch.setattr(neva, "_neva_loop", lambda _memory="": Loop())
    monkeypatch.setattr(neva, "_readable_views", AsyncMock(return_value=[]))
    monkeypatch.setattr(neva.neva_memory, "memory_note", AsyncMock(return_value=""))
    monkeypatch.setattr(neva.neva_memory, "learn", AsyncMock(return_value=0))
    try:
        response = await neva.send_message(
            "t-done", MessageRequest(content="Show it"),
            SimpleNamespace(is_disconnected=AsyncMock(return_value=False)), USER,
        )
        async for frame in response.body_iterator:
            if frame.startswith("event: done"):
                order.append("done")
    finally:
        thread_store.remove("t-done", user_name="alice")
    assert order[-2:] == ["stored:assistant:reply-1", "done"]


async def test_history_search_finds_only_the_caller_s_titles(monkeypatch):
    listing = AsyncMock(return_value=[
        {"thread_id": "t1", "title": "Sales per city", "created_at": "2026-11-09T00:00:00",
         "updated_at": "2026-11-09T00:00:00", "message_count": 2},
        {"thread_id": "t2", "title": "Stage upload", "created_at": "2026-11-09T00:00:00",
         "updated_at": "2026-11-09T00:00:00", "message_count": 4},
    ])
    monkeypatch.setattr(neva.assistant_repository, "list_threads", listing)
    found = await neva.list_threads(USER, None, None, "  SALES ")
    assert [thread.thread_id for thread in found.threads] == ["t1"]
    assert listing.await_args.kwargs == {"user_name": "alice"}


def test_the_first_question_names_the_conversation():
    assert neva._title_from("  Berapa total\nnet revenue   per kota? ") == (
        "Berapa total net revenue per kota?"
    )
    long = neva._title_from("x " * 100)
    assert len(long) == 80 and long.endswith("…")
