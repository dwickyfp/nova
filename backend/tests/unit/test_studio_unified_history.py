from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.agents import router
from app.modules.assistant.repository import AssistantThreadListUnavailable


def test_unified_history_checks_each_agent_and_preserves_recency(monkeypatch) -> None:
    now = datetime.now(UTC)
    rows = [
        {"thread_id": f"thread-{i}", "agent_id": agent, "title": agent,
         "created_at": now, "updated_at": now}
        for i, agent in enumerate(["sales", "__smart__", "denied", "sales", "__auto__"])
    ]
    listing = AsyncMock(return_value=rows)
    monkeypatch.setattr(router.assistant_repository, "list_threads", listing)

    async def authorized(agent_id: str, user: dict) -> dict:
        assert user["username"] == "alice"
        if agent_id == "denied":
            raise HTTPException(404, "Agent not found")
        return {"agent_id": agent_id}

    access = AsyncMock(side_effect=authorized)
    monkeypatch.setattr(router, "_require_agent", access)
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: {"username": "alice"}
    response = TestClient(app).get("/agents/threads")
    assert response.status_code == 200
    assert [row["thread_id"] for row in response.json()["threads"]] == [
        "thread-0", "thread-1", "thread-3", "thread-4",
    ]
    assert response.json()["count"] == 4
    listing.assert_awaited_once_with(user_name="alice", all_agents=True)
    assert access.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["history", "access"])
async def test_unified_history_reports_unavailability_without_claiming_empty(monkeypatch, source):
    listing = AsyncMock(return_value=[{"agent_id": "sales"}])
    access = AsyncMock(side_effect=HTTPException(503, "Agent metadata is temporarily unavailable"))
    if source == "history":
        listing.side_effect = AssistantThreadListUnavailable("temporary")
    monkeypatch.setattr(router.assistant_repository, "list_threads", listing)
    monkeypatch.setattr(router, "_require_agent", access)
    with pytest.raises(HTTPException) as unavailable:
        await router.list_studio_threads({"username": "alice"})
    assert unavailable.value.status_code == 503
