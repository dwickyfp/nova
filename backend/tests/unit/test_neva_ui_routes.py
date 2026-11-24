"""Neva threads: stored replies, history search, titles and stalled turns."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.assistant import router as neva
from app.modules.assistant.schemas import MessageRequest

USER = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"],
        "session_id": "device", "security_context_version": 1, "encrypted_password": "x"}


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


async def test_a_stalled_turn_ends_plainly_but_waiting_for_approval_does_not_count():
    import asyncio

    from app.modules.assistant import events

    async def stalled():
        yield events.text_delta("Looking it up")
        await asyncio.sleep(60)  # a step that never returns
        yield events.done("late")

    context = SimpleNamespace(intent_frame=SimpleNamespace(language="en"))
    frames = [frame async for frame in neva._within_turn_budget(stalled(), context, 0.3)]
    assert frames[-1].startswith("event: done") and '"timeout"' in frames[-1]
    assert "stopped without guessing" in frames[-2]

    async def waits_for_the_person():
        yield events.tool_call(SimpleNamespace(model_dump=lambda **_: {
            "tool_call_id": "c1", "tool_name": "query_execute", "status": "pending"}))
        await asyncio.sleep(0.6)  # the person reads the approval card
        yield events.tool_status("c1", "done")
        yield events.done("m1")

    frames = [frame async for frame in neva._within_turn_budget(
        waits_for_the_person(), context, 0.3)]
    assert frames[-1].startswith("event: done") and '"timeout"' not in frames[-1]
