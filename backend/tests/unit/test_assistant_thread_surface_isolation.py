"""Neva's thread endpoint cannot open or delete a Studio conversation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        raise AssertionError("Studio thread reached Neva's delete path")


@pytest.fixture
def studio_thread(monkeypatch):
    monkeypatch.setattr(assistant_router, "assistant_repository", _StudioThreadRepository())


async def test_neva_cannot_open_a_studio_thread(studio_thread):
    with pytest.raises(HTTPException) as error:
        await assistant_router._require_thread("studio-thread", "alice")
    assert error.value.status_code == 404


async def test_neva_cannot_delete_a_studio_thread(studio_thread):
    with pytest.raises(HTTPException) as error:
        await assistant_router.delete_thread("studio-thread", user={"username": "alice"})
    assert error.value.status_code == 404


async def test_studio_cannot_open_a_neva_thread(monkeypatch):
    class _NevaThreadRepository:
        async def get_thread(self, thread_id: str, *, user_name: str) -> dict:
            return {"thread_id": thread_id, "user_name": user_name, "agent_id": None}

    monkeypatch.setattr(agent_router, "assistant_repository", _NevaThreadRepository())
    with pytest.raises(HTTPException) as error:
        await agent_router._require_agent_thread("neva-thread", "studio-agent", "alice")
    assert error.value.status_code == 404


async def test_studio_trace_uses_the_agent_scope(monkeypatch):
    store = SimpleNamespace(
        get_thread=AsyncMock(return_value={"agent_id": None}), list_messages=AsyncMock()
    )

    @asynccontextmanager
    async def scope(owner):
        assert owner == "alice"
        yield store

    monkeypatch.setattr(observability, "_store", scope)
    assert (
        await observability.thread_trace(
            owner_name="alice",
            agent_id="studio-agent",
            thread_id="neva-thread",
        )
        is None
    )
    store.get_thread.assert_awaited_once_with("neva-thread")
    store.list_messages.assert_not_awaited()
