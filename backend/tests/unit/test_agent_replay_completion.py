"""A completed run with unavailable history must not keep replay clients polling."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.modules.agents import router
from app.modules.assistant import events


@pytest.fixture
def authorized_replay(monkeypatch):
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={"agent_id": "sales"}))
    monkeypatch.setattr(router, "_require_agent_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(router.run_journal, "has_security_version", AsyncMock(return_value=True))
    finish = AsyncMock()
    interrupt = AsyncMock()
    monkeypatch.setattr(router.run_journal, "finish", finish)
    monkeypatch.setattr(router.run_journal, "interrupt_stale", interrupt)
    yield
    finish.assert_not_awaited()
    interrupt.assert_not_awaited()


@pytest.mark.parametrize("after", [-1, 2])
async def test_completed_run_without_remaining_events_reports_unavailable_history(
    monkeypatch, authorized_replay, after,
):
    monkeypatch.setattr(router.run_journal, "get", AsyncMock(return_value={
        "status": "completed", "last_sequence": 3,
    }))
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(router.run_journal, "events_after", read)
    response = await router.replay_agent_run(
        "sales", "thread", "run", after,
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )

    async def collect():
        return [frame async for frame in response.body_iterator]

    frames = await asyncio.wait_for(collect(), timeout=0.25)
    assert [frame.splitlines()[0] for frame in frames] == ["event: error", "event: done"]
    payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
    assert payloads[0]["code"] == "run_history_unavailable"
    assert payloads[0]["sequence"] == after + 1
    assert payloads[1]["finish_reason"] == "error"
    assert payloads[1]["sequence"] == after + 2
    read.assert_awaited_once()


@pytest.mark.parametrize("complete", [False, True])
async def test_completed_replay_drains_saved_pages_before_deciding_history_is_missing(
    monkeypatch, authorized_replay, complete,
):
    monkeypatch.setattr(router.run_journal, "get", AsyncMock(return_value={
        "status": "completed", "last_sequence": 2,
    }))
    first = events.format_sse("text_delta", {"text": "Saved answer", "sequence": 0})
    second = events.format_sse("text_delta", {"text": " continues", "sequence": 1})
    done = events.format_sse("done", {
        "message_id": "reply", "finish_reason": "stop", "sequence": 2,
    })
    read = AsyncMock(side_effect=[[first], [second], [done] if complete else []])
    monkeypatch.setattr(router.run_journal, "events_after", read)
    response = await router.replay_agent_run(
        "sales", "thread", "run", -1,
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    async with asyncio.timeout(0.25):
        frames = [frame async for frame in response.body_iterator]
    assert frames[:2] == [first, second]
    assert read.await_count == 3
    if complete:
        assert frames == [first, second, done]
    else:
        assert [frame.splitlines()[0] for frame in frames[2:]] == ["event: error", "event: done"]
        assert json.loads(frames[2].split("data: ", 1)[1])["code"] == "run_history_unavailable"


@pytest.mark.parametrize("after", [2, 3])
async def test_already_consumed_completion_does_not_add_another_terminal_event(
    monkeypatch, authorized_replay, after,
):
    monkeypatch.setattr(router.run_journal, "get", AsyncMock(return_value={
        "status": "completed", "last_sequence": 2,
    }))
    monkeypatch.setattr(router.run_journal, "events_after", AsyncMock(return_value=[]))
    response = await router.replay_agent_run(
        "sales", "thread", "run", after,
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    async with asyncio.timeout(0.25):
        assert [frame async for frame in response.body_iterator] == []


@pytest.mark.parametrize("status", ["failed", "interrupted", "cancelled"])
@pytest.mark.parametrize("saved_completion", [False, True])
async def test_failed_replay_drains_every_page_before_reporting_completion(
    monkeypatch, authorized_replay, status, saved_completion,
):
    monkeypatch.setattr(router.run_journal, "get", AsyncMock(return_value={
        "status": status, "last_sequence": 201,
    }))
    history = [events.format_sse("text_delta", {"text": str(index), "sequence": index})
               for index in range(201)]
    if saved_completion:
        history.append(events.format_sse("done", {
            "message_id": "reply", "finish_reason": status, "sequence": 201,
        }))
    read = AsyncMock(side_effect=[history[:100], history[100:200], history[200:], []])
    monkeypatch.setattr(router.run_journal, "events_after", read)
    response = await router.replay_agent_run(
        "sales", "thread", "run", -1,
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    async with asyncio.timeout(0.25):
        frames = [frame async for frame in response.body_iterator]
    assert frames[:len(history)] == history
    assert read.await_count == (3 if saved_completion else 4)
    if saved_completion:
        assert frames == history
    else:
        assert len(frames) == len(history) + 2
        assert json.loads(frames[-1].split("data: ", 1)[1])["finish_reason"] == status
