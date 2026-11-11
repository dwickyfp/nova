from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.agents import studio_router

NOW = datetime.now(UTC)
SERVER = {
    "server_id": "server-1", "owner_name": "__nova__", "name": "Synthetic fixture",
    "description": "Arithmetic only", "transport": "http", "is_active": True,
    "endpoint": "https://fixture.example/mcp", "created_at": NOW, "updated_at": NOW,
}
TOOL = {
    "tool_id": "tool-1", "owner_name": "__nova__", "name": "fixture_total",
    "source": "mcp:server-1", "is_enabled": True, "created_at": NOW, "updated_at": NOW,
}
MUTATIONS = [
    ("POST", "/mcp-servers", {"name": "Synthetic fixture"}, "create_mcp_server", SERVER, 201),
    ("PATCH", "/mcp-servers/server-1", {"is_active": False}, "update_mcp_server", SERVER, 200),
    ("DELETE", "/mcp-servers/server-1", None, "delete_mcp_server", True, 204),
    ("POST", "/mcp-servers/server-1/discover", None, "update_mcp_server", SERVER, 200),
    ("PATCH", "/tools/tool-1", {"is_enabled": False}, "set_tool_enabled", TOOL, 200),
    ("DELETE", "/tools/tool-1", None, "set_tool_enabled", TOOL, 204),
]


def client_for(active_role, username="alice"):
    app = FastAPI()
    app.include_router(studio_router.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "username": username, "roles": ["ACCOUNTADMIN", "ANALYST"],
        "active_role": active_role,
    }
    return TestClient(app)


@pytest.mark.parametrize("operation", MUTATIONS, ids=[f"{x[0]} {x[1]}" for x in MUTATIONS])
@pytest.mark.parametrize("active_role", ["ANALYST", None, "ACCOUNTADMIN"])
def test_platform_mutations_require_active_accountadmin(monkeypatch, operation, active_role):
    method, path, body, operation_name, result, expected_status = operation
    repository = studio_router.agent_repository
    mutation = AsyncMock(return_value=result)
    monkeypatch.setattr(repository, "get_mcp_server", AsyncMock(return_value=SERVER))
    monkeypatch.setattr(repository, "get_tool", AsyncMock(
        side_effect=lambda _, owner_name: TOOL if owner_name == "__nova__" else None,
    ))
    monkeypatch.setattr(repository, operation_name, mutation)
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(studio_router.mcp_client, "list_tools", discover)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    with client_for(active_role) as client:
        response = client.request(method, path, json=body)
    if active_role == "ACCOUNTADMIN":
        assert response.status_code == expected_status, response.text
        mutation.assert_awaited_once()
        assert mutation.await_args.kwargs["owner_name"] == "__nova__"
    else:
        assert response.status_code == 403
        mutation.assert_not_awaited()
        discover.assert_not_awaited()


@pytest.mark.parametrize("active_role", ["ANALYST", None, "ACCOUNTADMIN"])
def test_endpoint_visibility_uses_active_role(monkeypatch, active_role):
    listing = AsyncMock(return_value=[{**SERVER, "command": "fixture-command", "args": ["x"]}])
    monkeypatch.setattr(studio_router.agent_repository, "list_mcp_servers", listing)
    with client_for(active_role) as client:
        response = client.get("/mcp-servers")
    assert response.status_code == 200
    server = response.json()["servers"][0]
    assert server["name"] == SERVER["name"]
    if active_role == "ACCOUNTADMIN":
        assert server["endpoint"] == SERVER["endpoint"]
    else:
        assert server["endpoint"] is None and server["command"] is None and server["args"] == []


def test_discovered_platform_tools_are_selectable_without_admin(monkeypatch):
    personal = {**TOOL, "tool_id": "personal", "owner_name": "alice", "source": "builtin"}
    hidden = {**TOOL, "tool_id": "internal", "source": "builtin"}
    listing = AsyncMock(
        side_effect=lambda owner_name: [personal] if owner_name == "alice" else [TOOL, hidden],
    )
    monkeypatch.setattr(studio_router.agent_repository, "list_tools", listing)
    monkeypatch.setattr(studio_router, "_seed_builtin_tools", AsyncMock())
    with client_for("ANALYST") as client:
        response = client.get("/tools")
    assert response.status_code == 200
    assert {t["tool_id"] for t in response.json()["tools"]} == {"personal", "tool-1"}
    assert response.json()["count"] == 2
    assert {call.kwargs["owner_name"] for call in listing.await_args_list} == {"alice", "__nova__"}


def test_personal_tool_toggle_does_not_need_platform_admin(monkeypatch):
    personal = {**TOOL, "owner_name": "alice", "source": "custom"}
    monkeypatch.setattr(
        studio_router.agent_repository, "get_tool", AsyncMock(return_value=personal),
    )
    mutation = AsyncMock(return_value={**personal, "is_enabled": False})
    monkeypatch.setattr(studio_router.agent_repository, "set_tool_enabled", mutation)
    with client_for("ANALYST") as client:
        response = client.patch("/tools/tool-1", json={"is_enabled": False})
    assert response.status_code == 200
    mutation.assert_awaited_once_with("tool-1", owner_name="alice", enabled=False)


def test_foreign_or_internal_non_mcp_tool_cannot_be_mutated(monkeypatch):
    async def lookup(_, *, owner_name):
        return {**TOOL, "source": "internal"} if owner_name == "__nova__" else None

    monkeypatch.setattr(studio_router.agent_repository, "get_tool", lookup)
    mutation = AsyncMock()
    monkeypatch.setattr(studio_router.agent_repository, "set_tool_enabled", mutation)
    with client_for("ACCOUNTADMIN") as client:
        assert client.patch("/tools/tool-1", json={"is_enabled": False}).status_code == 404
    mutation.assert_not_awaited()


def test_platform_owner_name_cannot_bypass_active_role(monkeypatch):
    monkeypatch.setattr(studio_router.agent_repository, "get_tool", AsyncMock(return_value=TOOL))
    mutation = AsyncMock()
    monkeypatch.setattr(studio_router.agent_repository, "set_tool_enabled", mutation)
    with client_for("ANALYST", username="__nova__") as client:
        assert client.patch("/tools/tool-1", json={"is_enabled": False}).status_code == 403
    mutation.assert_not_awaited()
