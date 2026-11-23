"""MCP connector governance decided by the owner on 2026-11-23.

Q1  ACCOUNTADMIN-registered connectors may reach loopback and private networks;
    link-local (cloud metadata), multicast and reserved addresses stay blocked.
Q2  Superseded the same day: Studio agents run every tool without asking and
    Neva has no MCP tools, so there is no per-tool read-only switch. Discover
    records each tool's annotations; unattended runs may call a tool its
    server declares read-only and not destructive, and nothing else.
Q3  Write-only auth headers: encrypted at rest, only their names are returned,
    never logged, sent only to the registered endpoint (no redirects).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.crypto import decrypt
from app.common.ssrf_guard import BlockedEndpointError, resolve_and_validate_url
from app.core.deps import get_current_user
from app.modules.agents import mcp_client, studio_router
from app.modules.agents.schemas import ToolView
from app.modules.agents.tools import mcp_tool
from app.modules.agents.tools.mcp_tool import McpToolRunner
from app.modules.assistant.tools import ToolInvocation

SECRET = "Bearer s3cr3t-fixture-token-value"
NOW = datetime.now(UTC)
SERVER = {
    "server_id": "server-1", "owner_name": "__nova__", "name": "Targets",
    "description": "", "transport": "http", "endpoint": "http://127.0.0.1:8299/mcp",
    "command": None, "args": [], "is_active": True, "last_status": None,
    "auth_header_names": [], "auth_headers_ciphertext": None,
    "created_at": NOW, "updated_at": NOW,
}
TOOL = {
    "tool_id": "tool-1", "owner_name": "__nova__", "name": "get_store_targets",
    "description": "", "source": "mcp:server-1", "input_schema": {}, "is_enabled": True,
    "annotations": {}, "created_at": NOW, "updated_at": NOW,
}


def client_for(active_role: str = "ACCOUNTADMIN") -> TestClient:
    app = FastAPI()
    app.include_router(studio_router.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "username": "nova_admin", "roles": ["ACCOUNTADMIN", "ANALYST"],
        "active_role": active_role, "session_id": "s1",
    }
    return TestClient(app)


# ── Q1: network reach ─────────────────────────────────────────────────


def test_connectors_use_the_private_network_guard(monkeypatch):
    seen: dict = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return httpx.AsyncClient()

    monkeypatch.setattr(mcp_client, "guarded_async_client", fake)
    monkeypatch.setattr(mcp_client.settings, "AI_PROVIDER_LOOPBACK_HOST", "host.docker.internal")
    mcp_client._client(5.0)
    assert seen["allow_private_network"] is True
    assert seen["loopback_host"] == "host.docker.internal"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8299/mcp", "http://10.1.2.3/mcp",
                                 "http://localhost:8299/mcp"])
def test_loopback_and_private_endpoints_are_allowed(url):
    resolve_and_validate_url(url, allow_private_network=True)


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/",
                                 "http://[fe80::1]/mcp", "http://224.0.0.1/mcp",
                                 "http://0.0.0.0/mcp"])
def test_metadata_and_other_special_ranges_stay_blocked(url):
    with pytest.raises(BlockedEndpointError):
        resolve_and_validate_url(url, allow_private_network=True)


async def test_discover_against_cloud_metadata_is_refused():
    with pytest.raises(mcp_client.McpError, match="not allowed"):
        await mcp_client.list_tools({**SERVER, "endpoint": "http://169.254.169.254/mcp"})


async def test_discover_on_a_stdio_server_names_the_step_that_works():
    """Before: the message offered "register its tools manually", which Nova cannot do."""
    stdio = {**SERVER, "transport": "stdio", "endpoint": None, "command": "uv run tools.py"}
    with pytest.raises(mcp_client.McpError) as raised:
        await mcp_client.list_tools(stdio)
    assert "add that address as an HTTP (Streamable) server" in str(raised.value)
    assert "manually" not in str(raised.value)


# ── Q2: annotations are information ───────────────────────────────────

READ_ONLY_HINT = {"readOnlyHint": True, "destructiveHint": False}


async def test_discover_records_annotations_as_information(monkeypatch):
    repository = studio_router.agent_repository
    monkeypatch.setattr(repository, "get_mcp_server", AsyncMock(return_value=SERVER))
    monkeypatch.setattr(repository, "update_mcp_server", AsyncMock(return_value=SERVER))
    monkeypatch.setattr(repository, "list_tools", AsyncMock(return_value=[]))
    upsert = AsyncMock()
    monkeypatch.setattr(repository, "upsert_tool", upsert)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(mcp_client, "list_tools", AsyncMock(return_value=[
        {"name": "get_store_targets", "description": "", "input_schema": {},
         "annotations": READ_ONLY_HINT},
    ]))
    await studio_router.discover_mcp_server(
        "server-1", {"username": "nova_admin", "active_role": "ACCOUNTADMIN"}
    )
    fields = upsert.await_args.kwargs["fields"]
    assert fields["annotations"] == READ_ONLY_HINT
    assert not {"read_only", "classification_source", "hint_changed"} & set(fields)


@pytest.mark.parametrize(
    ("annotations", "classification"),
    [
        ({}, "destructive"),
        (READ_ONLY_HINT, "read_only"),
        ({"readOnlyHint": True}, "read_only"),
        ({"readOnlyHint": True, "destructiveHint": True}, "destructive"),
        ({"readOnlyHint": False}, "destructive"),
        ({"readOnlyHint": "true"}, "destructive"),
    ],
)
def test_only_a_declared_read_only_lookup_counts_as_reading(annotations, classification):
    runner = McpToolRunner(SERVER, {**TOOL, "annotations": annotations})
    assert runner.classification == classification
    view = ToolView(**{**TOOL, "annotations": annotations}).model_dump()
    assert "runs_without_approval" not in view


def test_there_is_no_classification_endpoint():
    with client_for("ACCOUNTADMIN") as client:
        response = client.patch("/tools/tool-1/classification", json={"read_only": True})
    assert response.status_code in {404, 405}


# ── Q3: write-only auth headers ───────────────────────────────────────


def test_headers_are_encrypted_and_only_names_come_back(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    stored: dict = {}

    async def create(*, owner_name, fields):
        stored.update(fields)
        return {**SERVER, "auth_header_names": fields["auth_header_names"],
                "auth_headers_ciphertext": fields["auth_headers"]}

    monkeypatch.setattr(studio_router.agent_repository, "create_mcp_server", create)
    audit = AsyncMock()
    monkeypatch.setattr(studio_router, "write_audit_log", audit)
    with client_for() as client:
        response = client.post("/mcp-servers", json={
            "name": "Targets", "endpoint": SERVER["endpoint"],
            "auth_headers": {"Authorization": SECRET, "X-Api-Key": "k-123456"},
        })
    assert response.status_code == 201, response.text
    assert response.json()["auth_header_names"] == ["Authorization", "X-Api-Key"]
    assert SECRET not in response.text and "auth_headers" not in response.json()
    assert "enc:" not in response.text
    assert stored["auth_headers"].startswith("enc:") and SECRET not in stored["auth_headers"]
    assert json.loads(decrypt(stored["auth_headers"]))["Authorization"] == SECRET
    assert SECRET not in caplog.text
    assert SECRET not in json.dumps([c.kwargs for c in audit.await_args_list], default=str)


@pytest.mark.parametrize("headers", [
    {"Host": "x"}, {"Content-Type": "x"}, {"Bad Name": "x"}, {"Authorization": "a\nb"},
    {"Authorization": ""}, {f"X-{i}": "v" for i in range(9)},
])
def test_invalid_headers_are_refused_without_echoing_values(monkeypatch, headers):
    create = AsyncMock()
    monkeypatch.setattr(studio_router.agent_repository, "create_mcp_server", create)
    with client_for() as client:
        response = client.post("/mcp-servers", json={
            "name": "Targets", "endpoint": SERVER["endpoint"], "auth_headers": headers,
        })
    assert response.status_code == 422
    create.assert_not_awaited()
    assert "a\\nb" not in response.text


def test_update_replaces_or_clears_headers(monkeypatch):
    seen: list[dict] = []

    async def update(server_id, *, owner_name, fields):
        seen.append(fields)
        return SERVER

    monkeypatch.setattr(studio_router.agent_repository, "get_mcp_server",
                        AsyncMock(return_value=SERVER))
    monkeypatch.setattr(studio_router.agent_repository, "update_mcp_server", update)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    with client_for() as client:
        client.patch("/mcp-servers/server-1", json={"description": "x"})
        client.patch("/mcp-servers/server-1", json={"auth_headers": {}})
    assert "auth_headers" not in seen[0]
    assert seen[1] == {"auth_headers": None, "auth_header_names": []}


# ── Registration shape (live finding 2026-11-23) ─────────────────────
# Before: a non-URL endpoint or a stdio entry reached the metadata trigger and
# the administrator saw "Metadata operation violates a record constraint"; the
# form also had no command field, so a stdio server could never be saved.

@pytest.mark.parametrize(("body", "reason"), [
    ({"endpoint": "uv run tools.py --transport stdio"}, "http:// or https://"),
    ({"endpoint": "https://user:secret@tools.example.com/mcp"}, "Auth headers"),
    ({"endpoint": None}, "http:// or https://"),
    ({"transport": "stdio", "command": "  "}, "command that starts"),
])
def test_registration_the_store_would_refuse_gets_a_plain_reason(monkeypatch, body, reason):
    create = AsyncMock()
    monkeypatch.setattr(studio_router.agent_repository, "create_mcp_server", create)
    with client_for() as client:
        response = client.post("/mcp-servers", json={"name": "Probe", **body})
    assert response.status_code == 422
    assert reason in response.json()["detail"]
    assert "secret" not in response.text
    create.assert_not_awaited()


@pytest.mark.parametrize("body", [
    {"endpoint": "http://127.0.0.1:8299/mcp"},
    {"transport": "sse", "endpoint": "https://tools.example.com/sse"},
    {"transport": "stdio", "command": "uv run tools.py --transport stdio"},
])
def test_valid_registrations_are_saved(monkeypatch, body):
    create = AsyncMock(return_value={**SERVER, **body})
    monkeypatch.setattr(studio_router.agent_repository, "create_mcp_server", create)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    with client_for() as client:
        response = client.post("/mcp-servers", json={"name": "Probe", **body})
    assert response.status_code == 201, response.text
    create.assert_awaited_once()


def test_update_checks_the_merged_registration(monkeypatch):
    update = AsyncMock(return_value=SERVER)
    monkeypatch.setattr(studio_router.agent_repository, "get_mcp_server",
                        AsyncMock(return_value=SERVER))
    monkeypatch.setattr(studio_router.agent_repository, "update_mcp_server", update)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    with client_for() as client:
        moved = client.patch("/mcp-servers/server-1", json={"endpoint": "tools.example.com"})
        to_stdio = client.patch("/mcp-servers/server-1", json={"transport": "stdio"})
        renamed = client.patch("/mcp-servers/server-1", json={"name": "Targets 2"})
    assert moved.status_code == 422 and to_stdio.status_code == 422
    assert renamed.status_code == 200
    assert update.await_count == 1


def test_listing_hides_header_names_from_other_roles(monkeypatch):
    listing = AsyncMock(return_value=[{**SERVER, "auth_header_names": ["Authorization"],
                                       "auth_headers_ciphertext": "enc:abc"}])
    monkeypatch.setattr(studio_router.agent_repository, "list_mcp_servers", listing)
    with client_for("ANALYST") as client:
        body = client.get("/mcp-servers").json()
    assert body["servers"][0]["auth_header_names"] == []
    assert "enc:" not in json.dumps(body)


def _server_with_headers() -> dict:
    encrypted = studio_router._encrypted_auth_headers({"Authorization": SECRET})
    return {**SERVER, "auth_headers_ciphertext": encrypted["auth_headers"]}


async def test_headers_are_sent_to_the_endpoint_and_redirects_are_not_followed(
    monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host != "127.0.0.1":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": {}})
        if json.loads(request.read())["method"] == "tools/call":
            return httpx.Response(307, headers={"Location": "http://elsewhere.example/mcp"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    monkeypatch.setattr(
        mcp_client, "guarded_async_client",
        lambda **_: httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                      follow_redirects=True),
    )
    with pytest.raises(mcp_client.McpError, match="redirect"):
        await mcp_client.call_tool(_server_with_headers(), "get_store_targets", {})
    assert requests and all(r.url.host == "127.0.0.1" for r in requests)
    assert all(r.headers["authorization"] == SECRET for r in requests)
    assert SECRET not in caplog.text


async def test_refused_credentials_get_a_plain_error_without_the_value(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "guarded_async_client",
        lambda **_: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(401))
        ),
    )
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())
    runner = McpToolRunner(_server_with_headers(), TOOL)
    outcome = await runner.run(
        ToolInvocation("c1", runner.name, {"month": "2025-07"}),
        SimpleNamespace(user={"username": "alice", "active_role": "MARKETING"}),
    )
    assert not outcome.ok
    assert "Check the connector's auth headers" in outcome.error
    assert SECRET not in outcome.error
    preview = runner.preview(ToolInvocation("c1", runner.name, {"month": "2025-07"}))
    assert SECRET not in preview


async def test_unreadable_stored_headers_fail_closed():
    with pytest.raises(mcp_client.McpError, match="save them again"):
        await mcp_client.call_tool(
            {**SERVER, "auth_headers_ciphertext": "enc:not-a-token"}, "x", {}
        )
