"""A broken journal must end delivery and stop the next external effect."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.redis import session_store
from app.modules.agents import router
from app.modules.agents.mission import mission_service
from app.modules.assistant import events
from app.modules.assistant.schemas import AgentMessageRequest
from app.modules.assistant.tools import ToolRegistry


@pytest.mark.parametrize("fault", [
    "projection_error", "projection_timeout", "projection_and_finish_timeout",
    "journal_error", "journal_timeout", "history_error", "history_timeout",
])
async def test_storage_failure_closes_stream_and_loop_without_another_effect(
    monkeypatch, caplog, fault,
):
    monkeypatch.setattr(router, "RUN_STORAGE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(session_store, "_redis", SimpleNamespace(get=AsyncMock(return_value=None)))
    agent = {"agent_id": "fixture", "name": "Fixture", "owner_name": "alice"}
    monkeypatch.setattr(router, "_require_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(
        router, "_require_agent_thread", AsyncMock(return_value={"title": "Fixture"}),
    )
    monkeypatch.setattr(router.agent_service, "build_loop_inputs", AsyncMock(
        return_value=(ToolRegistry(), "System", 60, None),
    ))
    monkeypatch.setattr(router, "_generate_thread_title", AsyncMock(return_value="Fixture"))
    monkeypatch.setattr(router.assistant_repository, "list_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.assistant_repository, "rename_thread", AsyncMock())
    monkeypatch.setattr(
        router.assistant_repository, "learning_enabled", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(router.run_journal, "start", AsyncMock())
    closed = asyncio.Event()
    effects = []

    async def fail():
        if "timeout" in fault:
            await asyncio.Event().wait()
        raise RuntimeError("synthetic-storage-secret-canary")

    async def project(*_args):
        if fault.startswith("projection"):
            await fail()

    async def save_message(*_args, **kwargs):
        if fault.startswith("history") and kwargs.get("role") == "assistant":
            await fail()

    async def append_events(*_args):
        if fault.startswith("journal") and append.await_count == 1:
            await fail()

    async def finish_run(*_args):
        if fault == "projection_and_finish_timeout":
            await asyncio.Event().wait()

    saved = AsyncMock(side_effect=save_message)
    append = AsyncMock(side_effect=append_events)
    finish = AsyncMock(side_effect=finish_run)
    monkeypatch.setattr(router.assistant_repository, "append_message", saved)
    monkeypatch.setattr(router.run_journal, "append_batch", append)
    monkeypatch.setattr(router.run_journal, "finish", finish)
    monkeypatch.setattr(mission_service, "stream_update", project)

    class ScriptedLoop:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *, context, **_kwargs):
            context.mission_id = "fixture-mission" if fault.startswith("projection") else None
            try:
                if not fault.startswith("history"):
                    yield events.format_sse("tool_call", {
                        "tool_call_id": "fixture-call", "status": "pending", "name": "fixture",
                    })
                    effects.append("must not execute after failed progress publication")
                yield events.text_delta("Synthetic answer")
                yield events.done("reply", finish_reason="stop")
            finally:
                closed.set()

    monkeypatch.setattr(router, "AssistantLoop", ScriptedLoop)
    response = await router.send_agent_message(
        "fixture", "storage-failure-thread", AgentMessageRequest(content="Synthetic question"),
        SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
        {"username": "alice", "active_role": "analyst", "roles": ["analyst"]},
    )
    tasks = list(router._active_run_tasks)
    try:
        async with asyncio.timeout(1):
            frames = [frame async for frame in response.body_iterator]
            await asyncio.gather(*tasks)
        assert closed.is_set() and not effects
        assert "synthetic-storage-secret-canary" not in "".join(frames) + caplog.text
        done = [json.loads(frame.split("data: ", 1)[1]) for frame in frames
                if frame.startswith("event: done\n")]
        assert len(done) == 1 and done[0]["finish_reason"] == "interrupted"
        assert any('"code":"run_persistence_unavailable"' in frame for frame in frames)
        finish.assert_awaited_once()
        assert finish.await_args.args[1] == "interrupted"
        if not fault.startswith("history"):
            assistant = [call.kwargs for call in saved.await_args_list
                         if call.kwargs.get("role") == "assistant"]
            assert {"kind": "turn_outcome", "finish_reason": "interrupted"} in assistant[0]["steps"]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
