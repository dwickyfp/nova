"""Token renewal: a live session keeps its user signed in, an ended one does not.

Studio and the Dataviz frame renew the access token before it lapses, so a user
working in Superset is not sent to sign in while their Nova session is alive.
Renewal is not activity, and only the server-side session decides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.core import security
from app.core.config import settings
from app.modules.auth import router as module


def _token(sub="alice", sid="s1", expired_ago=timedelta(minutes=5), key=None):
    exp = datetime.now(UTC) - expired_ago
    return jwt.encode({"sub": sub, "sid": sid, "exp": exp}, key or settings.SECRET_KEY, "HS256")


@pytest.fixture
def client(monkeypatch):
    sessions = {"s1": {"username": "alice", "encrypted_password": "enc"}}
    refreshed = []

    async def get(sid):
        return sessions.get(sid)

    async def refresh(sid):
        refreshed.append(sid)

    monkeypatch.setattr(module.session_store, "get", get)
    monkeypatch.setattr(module.session_store, "refresh", refresh)
    monkeypatch.setattr(module, "decrypt_password", lambda value: "pw")
    app = FastAPI()
    app.include_router(module.router, prefix="/auth")
    return TestClient(app), sessions, refreshed


def _renew(http, token):
    return http.post("/auth/renew", headers={"Authorization": f"Bearer {token}"})


def test_an_expired_token_of_a_live_session_is_renewed_without_extending_it(client):
    http, _sessions, refreshed = client
    response = _renew(http, _token())
    assert response.status_code == 200
    fresh = security.decode_token(response.json()["access_token"])
    assert (fresh["sub"], fresh["sid"]) == ("alice", "s1")
    assert refreshed == []


@pytest.mark.parametrize(
    "token",
    [
        _token(sid="ended"),
        _token(sub="mallory"),
        _token(expired_ago=timedelta(hours=13)),
        _token(key="not-the-key-" * 4),
        "garbage",
    ],
)
def test_renewal_is_refused_without_a_live_matching_session(client, token):
    http, _sessions, _refreshed = client
    assert _renew(http, token).status_code == 401
