"""Regressions found by the end-to-end MCP connector test (fixture: tests/fixtures/mcp)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.agents import mcp_client, studio_router
from app.modules.agents.tools import mcp_tool
from app.modules.agents.tools.mcp_tool import McpToolRunner
from app.modules.assistant import events
from app.modules.assistant.events_v2 import StudioStreamEncoder
from app.modules.assistant.schemas import ToolCallView
from app.modules.assistant.tools import ToolInvocation

NOW = datetime.now(UTC)
SERVER = {
    "server_id": "server-1", "name": "Nova test tools", "transport": "http",
    "endpoint": "https://fixture.example/mcp", "is_active": True,
}
TOOL = {
    "tool_id": "tool-1", "name": "fx_convert", "description": "Convert currency",
    "source": "mcp:server-1", "is_enabled": True,
    "input_schema": {"type": "object", "properties": {"amount": {"type": "number"}}},
}
CONTEXT = SimpleNamespace(user={"username": "alice", "active_role": "MARKETING"})


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(mcp_tool, "write_audit_log", AsyncMock())


async def test_tool_reported_error_reaches_the_model_as_bounded_text(monkeypatch):
    """Before: every isError result became "The external tool failed." with no reason."""
    monkeypatch.setattr(mcp_client, "call_tool", AsyncMock(return_value={
        "isError": True,
        "content": [{"type": "text", "text": "Target service is unavailable " + "x" * 900}],
    }))
    runner = McpToolRunner(SERVER, TOOL)
    outcome = await runner.run(ToolInvocation("c1", runner.name, {"amount": 1}), CONTEXT)
    assert not outcome.ok
    assert "fx_convert reported an error" in outcome.error
    assert "Target service is unavailable" in outcome.error
    assert "not instructions" in outcome.error
    assert len(outcome.error) < 700


async def test_tool_error_text_with_a_credential_is_not_repeated(monkeypatch):
    monkeypatch.setattr(mcp_client, "call_tool", AsyncMock(return_value={
        "isError": True,
        "content": [{"type": "text", "text": "bad key sk-abcdefghijklmnopqrstuvwx"}],
    }))
    runner = McpToolRunner(SERVER, TOOL)
    outcome = await runner.run(ToolInvocation("c1", runner.name, {}), CONTEXT)
    assert not outcome.ok and "sk-" not in outcome.error


async def test_slow_server_reports_a_plain_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.read())["method"] == "tools/call":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    def client(**kwargs):
        assert isinstance(kwargs["timeout"], httpx.Timeout)
        assert kwargs["timeout"].read == 60.0
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mcp_client, "guarded_async_client", client)
    runner = McpToolRunner(SERVER, TOOL)
    outcome = await runner.run(ToolInvocation("c1", runner.name, {}), CONTEXT)
    assert not outcome.ok
    assert "did not answer within 60 seconds" in outcome.error


def test_runner_has_plain_label_and_preview_shows_arguments():
    runner = McpToolRunner(SERVER, TOOL)
    assert runner.display_name == "Nova test tools: fx convert"
    preview = runner.preview(
        ToolInvocation("c1", runner.name, {"amount": 250, "to_currency": "IDR"})
    )
    assert preview == (
        'Call external tool fx_convert on Nova test tools with {"amount": 250, "to_currency": "IDR"}'
    )
    hidden = runner.preview(ToolInvocation("c2", runner.name, {"api_key": "x"}))
    assert hidden.endswith("arguments that contain sensitive data") and '"x"' not in hidden


def test_v2_tool_part_carries_the_display_name():
    encoder = StudioStreamEncoder("run-1")
    frame = events.tool_call(ToolCallView(
        tool_call_id="c1", tool_name="mcp_tool1", display_name="Nova test tools: fx convert",
        classification="destructive", status="running",
    ))
    starts = [
        json.loads(item.split("data: ", 1)[1])
        for item in encoder.translate(frame)
        if item.startswith("event: part.start")
    ]
    assert starts and starts[0]["display_name"] == "Nova test tools: fx convert"


async def test_rediscovery_keeps_disabled_switch_and_disables_removed_tools(monkeypatch):
    """Before: Discover re-enabled every tool an administrator had switched off."""
    repository = studio_router.agent_repository
    monkeypatch.setattr(repository, "get_mcp_server", AsyncMock(return_value=SERVER))
    monkeypatch.setattr(repository, "update_mcp_server", AsyncMock(return_value=SERVER))
    monkeypatch.setattr(repository, "list_tools", AsyncMock(return_value=[
        {**TOOL, "is_enabled": False},
        {**TOOL, "tool_id": "tool-2", "name": "retired_tool"},
        {**TOOL, "tool_id": "tool-3", "name": "other", "source": "mcp:another"},
    ]))
    upsert = AsyncMock()
    disable = AsyncMock()
    monkeypatch.setattr(repository, "upsert_tool", upsert)
    monkeypatch.setattr(repository, "set_tool_enabled", disable)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(studio_router.mcp_client, "list_tools", AsyncMock(return_value=[
        {"name": "fx_convert", "description": "Convert", "input_schema": {}},
        {"name": "get_store_targets", "description": "Targets", "input_schema": {}},
    ]))
    result = await studio_router.discover_mcp_server(
        "server-1", {"username": "nova_admin", "active_role": "ACCOUNTADMIN"}
    )
    assert result.ok and result.tools_discovered == 2
    enabled = {c.kwargs["fields"]["name"]: c.kwargs["fields"]["is_enabled"] for c in upsert.await_args_list}
    assert enabled == {"fx_convert": False, "get_store_targets": True}
    disable.assert_awaited_once_with("tool-2", owner_name="__nova__", enabled=False)
