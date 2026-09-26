from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.agents import router as agents
from app.modules.assistant import router as assistant


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(agents.router, prefix="/agents")
    app.include_router(assistant.router, prefix="/assistant")
    app.dependency_overrides[get_current_user] = lambda: {"username": "alice"}
    return TestClient(app)


def test_filtered_page_keeps_cursor_to_next_accessible_page(client, monkeypatch):
    listing = AsyncMock(return_value=([{"agent_id": "denied"}], "next-page"))
    monkeypatch.setattr(agents.history_repository, "threads", listing)
    monkeypatch.setattr(agents, "_require_agent", AsyncMock(side_effect=HTTPException(404)))
    result = client.get("/agents/threads?limit=2")
    assert result.status_code == 200
    assert result.json() == {"threads": [], "count": 0, "next_cursor": "next-page"}
    listing.assert_awaited_once_with(user_name="alice", all_agents=True, limit=2, cursor=None)


@pytest.mark.parametrize("agent_id", ["__auto__", "__smart__"])
def test_smart_aliases_share_one_ordered_page(client, monkeypatch, agent_id):
    listing = AsyncMock(return_value=([], None))
    monkeypatch.setattr(agents.history_repository, "threads", listing)
    monkeypatch.setattr(agents, "_require_agent", AsyncMock(return_value={}))
    result = client.get(f"/agents/{agent_id}/threads?limit=3")
    assert result.status_code == 200
    listing.assert_awaited_once_with(
        user_name="alice", agent_ids=("__auto__", "__smart__"), limit=3, cursor=None
    )


@pytest.mark.parametrize("path", ["/assistant/threads", "/agents/threads"])
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "cursor=invalid"])
def test_invalid_pagination_is_rejected(client, monkeypatch, path, query):
    execute = AsyncMock()
    monkeypatch.setattr(
        assistant.history_repository.__class__.__module__ + ".db.execute_system", execute
    )
    assert client.get(f"{path}?{query}").status_code == 422
    execute.assert_not_awaited()


@pytest.mark.parametrize(
    "path, module, guard",
    [
        ("/assistant/threads/private", assistant, "_require_thread"),
        ("/agents/sales/threads/private", agents, "_require_agent_thread"),
    ],
)
def test_message_page_authorizes_thread_before_read(client, monkeypatch, path, module, guard):
    monkeypatch.setattr(agents, "_require_agent", AsyncMock(return_value={}))
    monkeypatch.setattr(module, guard, AsyncMock(side_effect=HTTPException(404)))
    messages = AsyncMock()
    monkeypatch.setattr(module.history_repository, "messages", messages)
    assert client.get(f"{path}?limit=50").status_code == 404
    messages.assert_not_awaited()
