"""Unit tests for the Tool Registry catalog, MCP client, and Studio preferences.

These cover the parts that are pure or mockable:

* the builtin tool catalog is well-formed and only exposes agent-bundleable
  names to the builder;
* the MCP client refuses stdio (an RCE surface), rejects a non-http endpoint,
  and normalizes an MCP tool descriptor;
* Studio preferences round-trip through defaults and reject an invalid theme.

No network, no engine.
"""

from __future__ import annotations

import pytest

from app.modules.agents import mcp_client, tool_catalog
from app.modules.agents.studio_schemas import StudioPreferences
from app.modules.agents.studio_service import _bool, _choice, _str_or_none


def test_builtin_catalog_is_well_formed() -> None:
    rows = tool_catalog.builtin_rows()
    assert rows
    for row in rows:
        assert row["source"] == "builtin"
        assert row["name"]
        assert row["description"]
        assert isinstance(row["input_schema"], dict)


def test_agent_bundleable_tools_exclude_authoring_tools() -> None:
    # An agent must not be offered the tools that create agents/models.
    assert "create_agent" not in tool_catalog.AGENT_BUNDLEABLE_TOOLS
    assert "create_semantic_model" not in tool_catalog.AGENT_BUNDLEABLE_TOOLS
    # ...but they are in the registry catalog (Nove uses them).
    catalog_names = {row["name"] for row in tool_catalog.builtin_rows()}
    assert {"create_agent", "create_semantic_model"} <= catalog_names


@pytest.mark.asyncio
async def test_mcp_refuses_stdio() -> None:
    with pytest.raises(mcp_client.McpError) as exc:
        await mcp_client.list_tools({"transport": "stdio", "command": "rm -rf /"})
    assert "stdio" in str(exc.value)


@pytest.mark.asyncio
async def test_mcp_rejects_unknown_transport() -> None:
    with pytest.raises(mcp_client.McpError):
        await mcp_client.list_tools({"transport": "carrier-pigeon"})


@pytest.mark.asyncio
async def test_mcp_requires_endpoint() -> None:
    with pytest.raises(mcp_client.McpError) as exc:
        await mcp_client.list_tools({"transport": "http", "endpoint": ""})
    assert "endpoint" in str(exc.value).lower()


def test_mcp_tool_normalization() -> None:
    normalized = mcp_client._tool_from_mcp(
        {"name": "get_weather", "description": "d", "inputSchema": {"type": "object"}}
    )
    assert normalized == {
        "name": "get_weather",
        "description": "d",
        "input_schema": {"type": "object"},
    }


def test_mcp_client_never_imports_a_process_runner() -> None:
    # The client must not spawn processes; stdio is refused, not executed.
    # Check imports, not the docstring prose (which names stdio to explain it).
    module = mcp_client
    assert "subprocess" not in dir(module)
    assert "os" not in dir(module)


def test_studio_preference_helpers() -> None:
    assert _str_or_none("  ") is None
    assert _str_or_none("x") == "x"
    assert _bool("true", False) is True
    assert _bool(None, True) is True
    assert _bool("no", True) is False
    assert _choice("dark", ("light", "dark", "system"), "system") == "dark"
    assert _choice("chartreuse", ("light", "dark", "system"), "system") == "system"


def test_studio_preferences_defaults() -> None:
    prefs = StudioPreferences()
    assert prefs.theme == "system"
    assert prefs.language == "en"
    assert prefs.extended_thinking is True
    assert prefs.role is None


def test_studio_router_literal_paths_are_registered_before_agent_dynamic() -> None:
    """The registries must not be shadowed by /{agent_id}.

    FastAPI matches in declaration order. studio_router has literal paths
    ("/tools", "/mcp-servers") that the agent router's dynamic "/{agent_id}"
    would capture if the agent router were included first. This asserts the
    studio router's paths exist and that the agent router declares its dynamic
    route, so the ordering in main.py is the only thing keeping them apart.
    """
    from app.modules.agents.router import router as agent_router
    from app.modules.agents.studio_router import router as studio_router

    studio_paths = {r.path for r in studio_router.routes}
    assert "/tools" in studio_paths
    assert "/mcp-servers" in studio_paths
    assert "/studio/settings" in studio_paths

    agent_paths = {r.path for r in agent_router.routes}
    assert "/{agent_id}" in agent_paths


# ── per-agent context token budget (NOVA-124) ────────────────────────────────


def test_token_budget_unset_or_invalid_uses_default() -> None:
    from app.modules.agents.service import _clamp_token_budget

    assert _clamp_token_budget(None) is None
    assert _clamp_token_budget(0) is None
    assert _clamp_token_budget(-5) is None
    assert _clamp_token_budget("48000") is None
    assert _clamp_token_budget(True) is None


def test_token_budget_is_clamped_to_a_sane_range() -> None:
    from app.modules.agents.service import (
        MAX_CONTEXT_TOKEN_BUDGET,
        MIN_CONTEXT_TOKEN_BUDGET,
        _clamp_token_budget,
    )

    assert _clamp_token_budget(10) == MIN_CONTEXT_TOKEN_BUDGET
    assert _clamp_token_budget(10_000_000) == MAX_CONTEXT_TOKEN_BUDGET
    assert _clamp_token_budget(48_000) == 48_000


@pytest.mark.asyncio
async def test_build_loop_inputs_returns_a_token_budget() -> None:
    from app.modules.agents.service import AgentService

    agent = {
        "agent_id": "a1",
        "database_name": "db",
        "schema_name": "s",
        "budget_seconds": None,
        "budget_tokens": 48_000,
        "default_tools": ["query_execute"],
        "default_skills": [],
    }
    registry, _prompt, seconds, tokens = await AgentService().build_loop_inputs(agent)
    assert registry.get("query_execute") is not None
    assert seconds == 60
    assert tokens == 48_000


def test_thread_title_is_the_question_bounded_to_one_line() -> None:
    from app.modules.agents.router import _thread_title

    assert _thread_title("Show revenue by product category") == (
        "Show revenue by product category"
    )
    # Whitespace collapses: a title is a single line in a sidebar.
    assert _thread_title("  Show\n revenue   by category ") == (
        "Show revenue by category"
    )


def test_thread_title_is_truncated_with_an_ellipsis() -> None:
    from app.modules.agents.router import _TITLE_MAX, _thread_title

    title = _thread_title("x" * 500)
    assert len(title) == _TITLE_MAX
    assert title.endswith("\u2026")
