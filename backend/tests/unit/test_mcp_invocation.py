import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.modules.agents import mcp_client, registry, router
from app.modules.agents.schemas import AgentCreateRequest, AgentUpdateRequest
from app.modules.agents.tools import mcp_tool
from app.modules.agents.tools.mcp_tool import McpToolRunner
from app.modules.assistant.tools import ToolInvocation, ToolRegistry


def _server() -> dict:
    return {
        "server_id": "server-1",
        "name": "CRM",
        "transport": "http",
        "endpoint": "https://crm.example/mcp",
        "is_active": True,
    }


def _tool() -> dict:
    return {
        "tool_id": "tool-1",
        "name": "lookup_customer",
        "description": "Lookup a customer",
        "source": "mcp:server-1",
        "is_enabled": True,
        "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
    }


def test_agent_requests_accept_diagnosis_and_selected_mcp_tool() -> None:
    selected = ["diagnose_change", "mcp:tool-1"]
    assert AgentCreateRequest(name="Sales", default_tools=selected).default_tools == selected
    assert AgentUpdateRequest(default_tools=selected).default_tools == selected


@pytest.mark.asyncio
async def test_agent_creation_rejects_unavailable_mcp_tool(monkeypatch):
    monkeypatch.setattr(router.agent_repository, "list_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.agent_repository, "list_mcp_servers", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as error:
        await router.create_agent(
            AgentCreateRequest(name="Sales", default_tools=["mcp:missing"]),
            {"username": "alice"},
        )
    assert error.value.status_code == 422
    assert "Unavailable MCP" in error.value.detail


@pytest.mark.asyncio
async def test_selected_mcp_tool_must_be_active_http_connector(monkeypatch):
    monkeypatch.setattr(
        router.agent_repository, "list_tools", AsyncMock(return_value=[_tool()])
    )
    servers = AsyncMock(return_value=[_server()])
    monkeypatch.setattr(router.agent_repository, "list_mcp_servers", servers)
    assert await router._unavailable_mcp_tools(["mcp:tool-1"]) == []
    servers.return_value = [{**_server(), "is_active": False}]
    assert await router._unavailable_mcp_tools(["mcp:tool-1"]) == ["mcp:tool-1"]


@pytest.mark.asyncio
async def test_selected_mcp_tool_requires_consent_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace(
        list_tools=AsyncMock(return_value=[_tool()]),
        list_mcp_servers=AsyncMock(return_value=[_server()]),
    )
    monkeypatch.setattr("app.modules.agents.repository.agent_repository", repository)
    tool_registry = ToolRegistry()
    await registry.add_mcp_tools(tool_registry, {"default_tools": ["mcp:tool-1"]})
    runner = tool_registry.get("mcp_tool1")
    assert runner is not None
    assert runner.classification == "destructive"
    assert runner.requires_consent
    call = AsyncMock(return_value={"content": [{"type": "text", "text": "Customer found"}]})
    audit = AsyncMock()
    monkeypatch.setattr(mcp_client, "call_tool", call)
    monkeypatch.setattr(mcp_tool, "write_audit_log", audit)
    result = await runner.run(
        ToolInvocation("c1", runner.name, {"id": "customer-1"}),
        SimpleNamespace(user={"username": "alice", "active_role": "sales"}),
    )
    assert result.ok and result.data == {"content": "Customer found"}
    call.assert_awaited_once()
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_mcp_rejects_secret_arguments_and_redacts_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = McpToolRunner(_server(), _tool())
    call = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "key sk-abcdefghijklmnopqrstuvwx"}],
        }
    )
    monkeypatch.setattr(mcp_client, "call_tool", call)
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())
    context = SimpleNamespace(user={"username": "alice", "active_role": "sales"})
    refused = await runner.run(ToolInvocation("c1", runner.name, {"api_key": "secret"}), context)
    assert not refused.ok
    call.assert_not_awaited()
    result = await runner.run(ToolInvocation("c2", runner.name, {"id": "1"}), context)
    assert result.ok and result.data == {"content": "[redacted]"}


@pytest.mark.asyncio
async def test_inactive_or_unselected_mcp_tool_is_not_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = {**_server(), "is_active": False}
    repository = SimpleNamespace(
        list_tools=AsyncMock(return_value=[_tool()]),
        list_mcp_servers=AsyncMock(return_value=[server]),
    )
    monkeypatch.setattr("app.modules.agents.repository.agent_repository", repository)
    tool_registry = ToolRegistry()
    await registry.add_mcp_tools(tool_registry, {"default_tools": ["mcp:tool-1"]})
    assert tool_registry.names() == []


@pytest.mark.asyncio
async def test_mcp_call_preserves_negotiated_session(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.read())
        if payload["method"] == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {}},
                headers={"Mcp-Session-Id": "session-123"},
            )
        if payload["method"] == "tools/call":
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 3,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            })
        return httpx.Response(202)

    @asynccontextmanager
    async def client_factory(**_kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.setattr(mcp_client, "guarded_async_client", client_factory)
    result = await mcp_client.call_tool(_server(), "lookup_customer", {"id": "1"})
    assert result["content"][0]["text"] == "ok"
    assert requests[-1].headers["Mcp-Session-Id"] == "session-123"
