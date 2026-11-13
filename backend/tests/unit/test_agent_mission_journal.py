"""Mission delivery survives reconnects and stops on uncertain persistence."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.redis import session_store
from app.modules.agents import router
from app.modules.agents.mission import mission_service
from app.modules.agents.public_projections import PublicMission
from app.modules.assistant import events
from app.modules.assistant.schemas import AgentMessageRequest
from app.modules.assistant.tools import ToolRegistry


@pytest.mark.parametrize("tool_boundary", [False, True])
@pytest.mark.parametrize("storage_fault", [None, "error", "timeout"])
async def test_mission_update_is_saved_before_delivery_and_terminal_completion(
    monkeypatch, caplog, tool_boundary, storage_fault,
):
    monkeypatch.setattr(router, "RUN_STORAGE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(session_store, "_redis", SimpleNamespace(get=AsyncMock(return_value=None)))
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value={
        "agent_id": "fixture", "name": "Fixture", "owner_name": "alice",
    }))
    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Fixture"}),
    )
    monkeypatch.setattr(router.agent_service, "build_loop_inputs", AsyncMock(
        return_value=(ToolRegistry(), "System", 60, None),
    ))
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Fixture"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(router.assistant_repository, "append_message", AsyncMock())
    monkeypatch.setattr(
        router.assistant_repository, "learning_enabled", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(router.run_journal, "start", AsyncMock())
    finish = AsyncMock()
    monkeypatch.setattr(router.run_journal, "finish", finish)
    persisted = []
    effects = []
    closed = asyncio.Event()

    async def append(_run_id, frames):
        if storage_fault and any(frame.startswith("event: mission_updated\n") for frame in frames):
            if storage_fault == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError("synthetic-mission-storage-secret")
        await asyncio.sleep(0)
        persisted.extend(frames)

    monkeypatch.setattr(router.run_journal, "append_batch", append)
    mission = PublicMission(
        mission_id="fixture-mission", thread_id="mission-journal-thread",
        objective="Synthetic objective", work_intent="ANALYZE", status="running", revision=2,
        created_at=datetime(2026, 1, 1, tzinfo=UTC), updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    project = AsyncMock(return_value=events.format_sse("mission_updated", {
        "mission": mission.model_dump(mode="json"),
    }))
    monkeypatch.setattr(mission_service, "stream_update", project)

    class ScriptedLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *, context, **_kwargs):
            context.mission_id = "fixture-mission"
            try:
                if tool_boundary:
                    yield events.format_sse("tool_call", {
                        "tool_call_id": "fixture-call", "status": "pending", "name": "fixture",
                    })
                    effects.append("effect after saved progress")
                yield events.text_delta("Synthetic answer")
                yield events.done("reply", finish_reason="stop")
            finally:
                closed.set()

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    response = await router.send_agent_message(
        "fixture", "mission-journal-thread", AgentMessageRequest(content="Synthetic question"),
        SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    tasks = list(router._active_run_tasks)
    frames = []
    try:
        async with asyncio.timeout(1):
            async for frame in response.body_iterator:
                if frame.startswith("event: mission_updated\n") or (
                    not storage_fault and frame.startswith("event: done\n")
                ):
                    assert frame in persisted
                frames.append(frame)
            await asyncio.gather(*tasks)
        assert closed.is_set()
        assert "synthetic-mission-storage-secret" not in "".join(frames) + caplog.text
        assert frames == persisted
        payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
        sequences = [payload["sequence"] for payload in payloads]
        assert sequences == sorted(set(sequences))
        assert frames[-1].startswith("event: done\n")
        assert sum(frame.startswith("event: done\n") for frame in frames) == 1
        if storage_fault:
            assert not effects
            assert payloads[-1]["finish_reason"] == "interrupted"
            assert payloads[-2]["code"] == "run_persistence_unavailable"
            assert not any(frame.startswith("event: mission_updated\n") for frame in frames)
            assert finish.await_args.args[1] == "interrupted"
        else:
            assert len(effects) == int(tool_boundary)
            assert payloads[-1]["finish_reason"] == "stop"
            assert sum(frame.startswith("event: mission_updated\n") for frame in frames) == (
                2 if tool_boundary else 1
            )
            assert finish.await_args.args[1] == "completed"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
