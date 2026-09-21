"""Tool registry for the assistant (Stage C: ``query_execute`` registered).

Stage B defined the boundary the loop uses; this module registers the v1 tool
against it. The registry is built here rather than in the loop so the loop's
control flow does not change when a tool is added.

Importing this module is what registers ``query_execute``: the tool module
imports the registry dataclasses, and this module imports the tool instance, so
the dependency is one-directional (``registry`` → ``tools.query_execute`` →
``tools``) and cannot cycle.
"""

from __future__ import annotations

from app.modules.assistant.tools import (
    AssistantTool,
    ToolInvocation,
    ToolOutcome,
    ToolRegistry,
)

__all__ = [
    "AssistantTool",
    "ToolInvocation",
    "ToolOutcome",
    "ToolRegistry",
    "tool_registry",
]


def build_registry() -> ToolRegistry:
    """Return a registry with the v1 tools registered.

    A factory (rather than only the process-wide instance) so tests get an
    isolated registry.
    """
    from app.modules.assistant.tools.load_skill import load_skill_tool
    from app.modules.assistant.tools.query_execute import query_execute_tool

    registry = ToolRegistry()
    registry.register(load_skill_tool)
    registry.register(query_execute_tool)

    # Agent Studio authoring tools (Phase 12). Nove can draft a semantic model or
    # an agent from a request. Both are write tools: classified ``destructive``
    # so they always require explicit approval and are never auto-approved by the
    # read-only grant. Imported lazily so a deployment without the agents module
    # still builds the base registry.
    try:
        from app.modules.agents.tools.create_agent import create_agent_tool
        from app.modules.agents.tools.create_semantic_model import (
            create_semantic_model_tool,
        )

        registry.register(create_semantic_model_tool)
        registry.register(create_agent_tool)
    except ImportError:  # pragma: no cover - module always present in this tree
        pass

    return registry


#: Process-wide registry the router's loop uses.
tool_registry = build_registry()
