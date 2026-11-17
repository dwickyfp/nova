"""Workspace file download: bytes as stored, safe headers, workspace access first."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.deps import get_current_user
from app.core.exceptions import StorageError, register_exception_handlers
from app.modules.workspaces import router as module

USER = {"username": "alice", "active_role": "marketing", "assigned_roles": ["marketing"],
        "session_id": "s", "security_context_version": 1}


@pytest.fixture
async def client(monkeypatch):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/workspaces")
    app.dependency_overrides[get_current_user] = lambda: USER
    spaces = SimpleNamespace(for_entry=AsyncMock(return_value=SimpleNamespace(namespace="~ws/abc")))
    files = SimpleNamespace(read_entry_bytes=AsyncMock(
        return_value=({"name": "Sales Q4 2026-11-16 0700.xlsx"}, b"PK\x03\x04binary")))
    monkeypatch.setattr(module, "space_service", spaces)
    monkeypatch.setattr(module, "workspace_service", files)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as http:
        yield http, spaces, files


async def test_download_returns_the_stored_bytes_as_an_attachment(client):
    http, spaces, files = client
    response = await http.get("/workspaces/files/e1/download")
    assert response.status_code == 200
    assert response.content == b"PK\x03\x04binary"
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''Sales%20Q4%202026-11-16%200700.xlsx")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    spaces.for_entry.assert_awaited_once_with(USER, "e1")
    files.read_entry_bytes.assert_awaited_once_with("~ws/abc", "e1")


async def test_a_file_outside_the_viewers_workspaces_is_not_found(client):
    http, spaces, files = client
    spaces.for_entry.side_effect = StorageError("Not found", status_code=404)
    response = await http.get("/workspaces/files/e1/download")
    assert response.status_code == 404
    files.read_entry_bytes.assert_not_awaited()
