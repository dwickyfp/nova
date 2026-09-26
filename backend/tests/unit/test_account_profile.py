import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_user
from app.modules.auth import profile
from app.modules.auth.router import router


@pytest.fixture
def profile_api(monkeypatch):
    records = {}

    async def execute(sql, params):
        username, key = params[:2]
        assert key == profile.PROFILE_KEY
        if sql.startswith("INSERT"):
            records[username] = params[2]
            return {"rows": []}
        return {"rows": [[records[username]]] if username in records else []}

    monkeypatch.setattr(profile.db, "execute_system", execute)
    audit = AsyncMock()
    monkeypatch.setattr(profile, "write_audit_log", audit)
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "alice",
        "session_id": "session-1",
    }
    return app, records, audit


async def test_profile_roundtrip_is_scoped_to_authenticated_user(profile_api):
    app, records, audit = profile_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial = await client.get("/auth/profile")
        assert initial.json() == {
            "username": "alice",
            "first_name": "",
            "last_name": "",
            "email": "",
        }
        fields = {"first_name": "  Alice ", "last_name": "O'Neil", "email": "alice@example.com"}
        response = await client.put("/auth/profile", json=fields)
        assert response.status_code == 200
        assert response.json()["first_name"] == "Alice"
        assert (await client.get("/auth/profile")).json() == response.json()
        assert json.loads(records["alice"])["last_name"] == "O'Neil"
        audit.assert_awaited_once()
        assert audit.call_args.kwargs["user_name"] == "alice"
        assert "alice@example.com" not in str(audit.call_args)

        app.dependency_overrides[get_current_user] = lambda: {"username": "bob"}
        assert (await client.get("/auth/profile")).json()["email"] == ""
        await client.put("/auth/profile", json={"first_name": "Bob"})
        assert json.loads(records["alice"])["first_name"] == "Alice"


@pytest.mark.parametrize(
    "fields",
    [
        {"username": "bob"},
        {"password": "secret"},
        {"email": "invalid"},
        {"first_name": "a" * 101},
        {"email": "a b@example.com"},
    ],
)
async def test_profile_rejects_invalid_or_identity_fields(profile_api, fields):
    app, records, audit = profile_api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/auth/profile", json=fields)
    assert response.status_code == 422
    assert records == {}
    audit.assert_not_awaited()


async def test_profile_store_failure_is_not_an_empty_profile(profile_api, monkeypatch):
    app, _, _ = profile_api
    monkeypatch.setattr(
        profile.db, "execute_system", AsyncMock(side_effect=RuntimeError("offline"))
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/auth/profile")
    assert response.status_code == 500


async def test_profile_requires_authentication():
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/auth/profile")).status_code in (401, 403)
        assert (await client.put("/auth/profile", json={})).status_code in (401, 403)
