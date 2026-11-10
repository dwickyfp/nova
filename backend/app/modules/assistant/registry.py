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
    from app.modules.agents.tools.ai_search import ai_search_tool
    from app.modules.agents.tools.data_to_chart import data_to_chart_tool
    from app.modules.agents.tools.intelligence_views import (
        feature_lookup_tool,
        semantic_view_query_tool,
    )
    from app.modules.agents.tools.ml_execute import ml_execute_tool
    from app.modules.assistant.tools.catalog_search import catalog_search_tool
    from app.modules.assistant.tools.client_capability import invoke_client_capability_tool
    from app.modules.assistant.tools.create_semantic_view import create_semantic_view_tool
    from app.modules.assistant.tools.inspect_agent_configuration import (
        inspect_agent_configuration_tool,
    )
    from app.modules.assistant.tools.load_skill import load_skill_tool
    from app.modules.assistant.tools.provision_user import provision_user_tool
    from app.modules.assistant.tools.query_context import (
        inspect_query_error_tool,
        verify_query_repair_tool,
    )
    from app.modules.assistant.tools.query_execute import query_execute_tool
    from app.modules.assistant.tools.query_mutate import query_mutate_tool
    from app.modules.assistant.tools.role_access import (
        grant_role_access_tool,
        inspect_role_access_tool,
    )
    from app.modules.assistant.tools.search_knowledge import search_knowledge_tool
    from app.modules.assistant.tools.validate_sql import validate_sql_tool

    registry = ToolRegistry()
    registry.register(load_skill_tool)
    registry.register(search_knowledge_tool)
    registry.register(catalog_search_tool)
    registry.register(query_execute_tool)
    from app.modules.agents.tools.compute_metrics import compute_metrics_tool

    registry.register(compute_metrics_tool)
    registry.register(ml_execute_tool)
    registry.register(data_to_chart_tool)
    registry.register(ai_search_tool)
    registry.register(semantic_view_query_tool)
    registry.register(feature_lookup_tool)
    registry.register(inspect_role_access_tool)
    registry.register(grant_role_access_tool)
    registry.register(provision_user_tool)
    registry.register(query_mutate_tool)
    registry.register(validate_sql_tool)
    registry.register(invoke_client_capability_tool)
    registry.register(inspect_agent_configuration_tool)
    registry.register(inspect_query_error_tool)
    registry.register(verify_query_repair_tool)
    registry.register(create_semantic_view_tool)

    from app.modules.assistant.intelligence import SkillDefinition
    from app.modules.assistant.skill_registry import skill_library

    for skill in skill_library.skills:
        registry.skill_definitions[skill.name] = SkillDefinition(
            name=skill.name,
            summary=skill.summary,
            triggers=skill.triggers,
            body=skill.body,
            trust_level="platform_skill",
        )
    registry.discoverable_skills = tuple(skill_library.names())

    # Agent creation remains separate from Semantic View authoring. Both write
    # operations require consent; the legacy semantic-model creator is hidden.
    try:
        from app.modules.agents.tools.create_agent import create_agent_tool

        registry.register(create_agent_tool)
    except ImportError:  # pragma: no cover - module always present in this tree
        pass

    return registry


#: Process-wide registry of the console tools (the capability inventory).
tool_registry = build_registry()


def build_neva_registry() -> ToolRegistry:
    """Neva's tools: every console tool, plus governed business answers.

    ``semantic_query`` answers metric questions from the Semantic Views the
    caller's active role can open, with ontology grounding; free SQL, fixes and
    administration stay available through the console tools.
    """
    registry = ToolRegistry()
    for tool in tool_registry._tools.values():  # noqa: SLF001 - copy the inventory
        registry.register(tool)
    from app.modules.agents.tools.semantic_query import semantic_query_tool
    from app.modules.assistant.tools.semantic_views import list_semantic_views_tool

    registry.register(semantic_query_tool)
    registry.register(list_semantic_views_tool)
    # The business glossary: what a term officially means, the release in use,
    # and proposals the person files or withdraws (each with consent).
    from app.modules.agents.tools.ontology import (
        knowledge_propose_tool,
        knowledge_withdraw_tool,
        ontology_explain_tool,
        ontology_resolve_tool,
        ontology_status_tool,
    )

    for tool in (ontology_resolve_tool, ontology_explain_tool, ontology_status_tool,
                 knowledge_propose_tool, knowledge_withdraw_tool):
        registry.register(tool)
    return registry
