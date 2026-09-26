"""Per-agent tool registry composition (Phase 12, N12-B2).

The plain assistant registers a fixed set of tools once
(``app.modules.assistant.registry``). An agent chooses which tools it bundles, so
its registry is built per agent from that selection.

The selection is **structural**: a tool the owner did not choose is not put in
the registry, so the loop neither advertises it nor can the model call it. This
is stronger than hiding it in the prompt.

``load_skill`` is always present when the agent also selects it; it is a pure,
credential-free reader, and a missing skill catalog simply means the model does
not load one.
"""

from __future__ import annotations

from typing import Any

from app.modules.assistant.tools import ToolRegistry

#: Names Nova can actually provide. A name outside this set is a bug in the
#: caller (the router validates at write time); building a registry ignores it
#: rather than failing a turn.
KNOWN_TOOLS = frozenset(
    {
        "load_skill",
        "query_execute",
        "semantic_query",
        "semantic_search",
        "ai_search",
        "semantic_view_query",
        "feature_lookup",
        "data_to_chart",
        "diagnose_change",
        "ml_execute",
    }
)


def build_registry(agent: dict[str, Any]) -> ToolRegistry:
    """Return the registry for one agent from its ``default_tools`` selection.

    Custom tools are selected by name with a ``custom:`` prefix (so a custom tool
    never collides with a builtin). They are resolved from the owner's stored
    custom tools; a name that no longer exists is skipped.
    """
    selected = {t for t in (agent.get("default_tools") or []) if t in KNOWN_TOOLS}
    registry = ToolRegistry()

    if "load_skill" in selected:
        from app.modules.assistant.tools.load_skill import load_skill_tool

        registry.register(load_skill_tool)

    # The semantic/agent tools live in this module's own ``tools`` package.
    # Imported lazily so a deployment that has not built them yet (they land in
    # later stages) still composes the tools it does have.
    if "semantic_query" in selected:
        try:
            from app.modules.agents.tools.semantic_query import semantic_query_tool

            registry.register(semantic_query_tool)
        except ImportError:  # pragma: no cover - stage ordering only
            pass

    if "semantic_search" in selected:
        try:
            from app.modules.agents.tools.semantic_search import semantic_search_tool

            registry.register(semantic_search_tool)
        except ImportError:  # pragma: no cover - stage ordering only
            pass

    if "ai_search" in selected:
        from app.modules.agents.tools.ai_search import AISearchTool

        registry.register(AISearchTool(agent.get("resource_bindings")))

    if "semantic_view_query" in selected or "feature_lookup" in selected:
        from app.modules.agents.tools.intelligence_views import (
            FeatureLookupTool,
            semantic_view_query_tool,
        )

        if "semantic_view_query" in selected:
            registry.register(semantic_view_query_tool)
        if "feature_lookup" in selected:
            registry.register(FeatureLookupTool(agent.get("resource_bindings")))

    if "data_to_chart" in selected:
        try:
            from app.modules.agents.tools.data_to_chart import data_to_chart_tool

            registry.register(data_to_chart_tool)
        except ImportError:  # pragma: no cover - stage ordering only
            pass

    if "diagnose_change" in selected:
        from app.modules.agents.tools.diagnose_change import diagnose_change_tool

        registry.register(diagnose_change_tool)

    if "ml_execute" in selected:
        from app.modules.agents.tools.ml_execute import ml_execute_tool

        registry.register(ml_execute_tool)

    return registry


async def add_custom_tools(
    registry: ToolRegistry, agent: dict[str, Any]
) -> None:
    """Register the agent's selected custom tools onto ``registry``.

    Async because it reads the owner's stored custom tools. A ``custom:<name>``
    selection that no longer resolves is skipped rather than failing the turn, so
    deleting a tool cannot break every agent that referenced it.
    """
    selected = [
        t.split(":", 1)[1]
        for t in (agent.get("default_tools") or [])
        if isinstance(t, str) and t.startswith("custom:")
    ]
    if not selected:
        return
    owner = agent.get("owner_name")
    if not owner:
        return

    from app.modules.agents.repository import agent_repository
    from app.modules.agents.tools.custom_tool import CustomToolRunner

    wanted = set(selected)
    tools = await agent_repository.list_custom_tools(owner_name=owner)
    for tool in tools:
        if tool["name"] in wanted:
            registry.register(CustomToolRunner(tool))


async def add_mcp_tools(registry: ToolRegistry, agent: dict[str, Any]) -> None:
    """Register only explicitly selected, enabled HTTP connector tools."""
    selected = {
        value.split(":", 1)[1]
        for value in (agent.get("default_tools") or [])
        if isinstance(value, str) and value.startswith("mcp:")
    }
    if not selected:
        return
    from app.modules.agents.repository import agent_repository
    from app.modules.agents.tools.mcp_tool import McpToolRunner

    tools = await agent_repository.list_tools(owner_name="__nova__")
    servers = {
        server["server_id"]: server
        for server in await agent_repository.list_mcp_servers(owner_name="__nova__")
    }
    for tool in tools:
        if tool["tool_id"] not in selected or not tool.get("is_enabled"):
            continue
        source = str(tool.get("source") or "")
        if not source.startswith("mcp:"):
            continue
        server = servers.get(source.split(":", 1)[1])
        if server and server.get("is_active") and server.get("transport") == "http":
            registry.register(McpToolRunner(server, tool))
