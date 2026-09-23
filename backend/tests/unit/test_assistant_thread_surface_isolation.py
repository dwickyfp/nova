"""Nove's thread endpoint cannot open or delete a Studio conversation."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.agents import observability
from app.modules.agents import router as agent_router
from app.modules.assistant import router as assistant_router


class _StudioThreadRepository:
    async def get_thread(self, thread_id: str, *, user_name: str) -> dict:
        return {
            "thread_id": thread_id,
            "user_name": user_name,
            "agent_id": "studio-agent",
        }

    async def delete_thread(self, thread_id: str, *, user_name: str) -> bool:
        raise AssertionError("Studio thread reached Nove's delete path")


@pytest.fixture
def studio_thread(monkeypatch):
    monkeypatch.setattr(assistant_router, "assistant_repository", _StudioThreadRepository())


async def test_nove_cannot_open_a_studio_thread(studio_thread):
    with pytest.raises(HTTPException) as error:
        await assistant_router._require_thread("studio-thread", "alice")
    assert error.value.status_code == 404


async def test_nove_cannot_delete_a_studio_thread(studio_thread):
    with pytest.raises(HTTPException) as error:
        await assistant_router.delete_thread("studio-thread", user={"username": "alice"})
    assert error.value.status_code == 404


async def test_studio_cannot_open_a_nove_thread(monkeypatch):
    class _NoveThreadRepository:
        async def get_thread(self, thread_id: str, *, user_name: str) -> dict:
            return {"thread_id": thread_id, "user_name": user_name, "agent_id": None}

    monkeypatch.setattr(agent_router, "assistant_repository", _NoveThreadRepository())
    with pytest.raises(HTTPException) as error:
        await agent_router._require_agent_thread("nove-thread", "studio-agent", "alice")
    assert error.value.status_code == 404


async def test_studio_trace_uses_the_agent_scope(monkeypatch):
    class _RecordingDB:
        async def execute_system(self, sql: str, params: list) -> dict:
            assert "agent_id = %s" in sql
            assert params == ["nove-thread", "alice", "studio-agent"]
            return {"rows": []}

    monkeypatch.setattr(observability, "db", _RecordingDB())
    assert await observability.thread_trace(
        owner_name="alice", agent_id="studio-agent", thread_id="nove-thread"
    ) is None
