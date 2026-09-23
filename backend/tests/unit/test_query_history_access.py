from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.query.router import router
from app.modules.query.service import query_service


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/query")
    subject = {"username": "alice", "roles": ["analyst", "ACCOUNTADMIN"], "active_role": "analyst"}

    async def current_user():
        return subject

    app.dependency_overrides[get_current_user] = current_user
    history = AsyncMock(return_value={"items": [], "total": 0})
    stats = AsyncMock(
        return_value={
            "total": 0,
            "avg_duration_ms": None,
            "error_count": 0,
            "success_count": 0,
            "error_rate": 0,
        }
    )
    monkeypatch.setattr(query_service, "get_history", history)
    monkeypatch.setattr(query_service, "get_history_stats", stats)
    return TestClient(app), subject, history, stats


@pytest.mark.parametrize("path", ["/query/history", "/query/history/stats"])
def test_other_user_history_requires_active_admin(client, path):
    http, subject, history, stats = client

    response = http.get(path, params={"user_name": "bob"})

    assert response.status_code == 403
    history.assert_not_awaited()
    stats.assert_not_awaited()

    subject["active_role"] = "ACCOUNTADMIN"
    response = http.get(path, params={"user_name": "bob"})

    assert response.status_code == 200
    assert (history if path.endswith("history") else stats).await_args.kwargs["username"] == "bob"


def test_own_history_and_page_limits(client):
    http, subject, history, _ = client

    assert http.get("/query/history", params={"user_name": "alice"}).status_code == 200
    assert history.await_args.kwargs["username"] == "alice"
    assert http.get("/query/history", params={"limit": 501}).status_code == 422
    assert http.get("/query/history", params={"offset": -1}).status_code == 422
    assert history.await_count == 1
