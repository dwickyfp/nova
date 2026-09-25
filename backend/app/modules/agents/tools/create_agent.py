"""``create_agent`` — let Nove assemble an Agent Studio agent from a request.

A **write** tool (it creates a ``CONFIG_AGENTS`` row), classified
``destructive`` so it always needs explicit approval and is never covered by the
read-only conversation grant.

The agent references a semantic model by id; the tool validates that the model
belongs to the caller before binding it, so a prompt cannot point an agent at
another user's model. The tool descriptions it sets are the same names the
builder UI writes, so an agent created by Nove behaves identically to one made
by hand.
"""

from __future__ import annotations

import logging
from typing import Any

from app.common.audit import write_audit_log
from app.modules.agents.repository import agent_repository
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

logger = logging.getLogger(__name__)

#: The tools an agent may bundle. Mirrors the router's allow-list.
_ALLOWED_TOOLS = {
    "load_skill",
    "query_execute",
    "semantic_query",
    "semantic_search",
    "ai_search",
    "semantic_view_query",
    "feature_lookup",
    "data_to_chart",
    "ml_execute",
}

_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Display name, e.g. 'Revenue Analyst'.",
        },
        "description": {
            "type": "string",
            "description": "One line describing what the agent does.",
        },
        "instructions_response": {
            "type": "string",
            "description": "How the agent should answer.",
        },
        "instructions_orchestration": {
            "type": "string",
            "description": "How the agent should choose tools and work the request.",
        },
        "semantic_view_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Names or IDs of published Semantic Views to bind to this agent.",
        },
        "semantic_model_name": {
            "type": "string",
            "description": (
                "Legacy input for one published Semantic View name. "
                "Use semantic_view_names for new calls."
            ),
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Tools to bundle. Valid: load_skill, semantic_query, "
                "semantic_search, ai_search, semantic_view_query, feature_lookup, "
                "query_execute, data_to_chart, ml_execute."
            ),
        },
        "sample_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Suggested starter questions shown in Nova Studio.",
        },
    },
    "required": ["name"],
}


class CreateAgentTool:
    """Creates an Agent Studio agent owned by the requesting user."""

    name = "create_agent"
    description = (
        "Create an Agent Studio agent with instructions, tools, and an optional "
        "published Semantic Views. Requires your approval before it writes."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "destructive"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        name = str(invocation.arguments.get("name") or "").strip()
        names = invocation.arguments.get("semantic_view_names")
        if not isinstance(names, list):
            names = [invocation.arguments.get("semantic_model_name")]
        views = ", ".join(str(name) for name in names if name)
        tools = invocation.arguments.get("tools") or []
        tool_list = ", ".join(str(t) for t in tools) if isinstance(tools, list) else ""
        suffix = f" (Semantic Views: {views})" if views else ""
        return f"create agent `{name}`{suffix} with tools: {tool_list or 'default'}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        owner = getattr(context, "user_name", None)
        if not owner:
            return ToolOutcome(ok=False, summary="", error="No user context is available.")

        name = str(invocation.arguments.get("name") or "").strip()
        if not name:
            return ToolOutcome(ok=False, summary="", error="An agent name is required.")

        tools = invocation.arguments.get("tools")
        if isinstance(tools, list):
            invalid = [str(tool) for tool in tools if str(tool) not in _ALLOWED_TOOLS]
            if invalid:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=f"Unsupported agent tools: {', '.join(invalid)}.",
                )
            selected = [str(t) for t in tools if str(t) in _ALLOWED_TOOLS]
        else:
            selected = ["load_skill", "semantic_query", "data_to_chart"]
        if not selected:
            selected = ["load_skill"]

        names = invocation.arguments.get("semantic_view_names")
        if names is None:
            old_name = str(invocation.arguments.get("semantic_model_name") or "").strip()
            names = [old_name] if old_name else []
        if not isinstance(names, list) or len(names) > 16 or any(
            not isinstance(item, str) or not item.strip() for item in names
        ):
            return ToolOutcome(ok=False, summary="", error="Select at most 16 Semantic Views.")
        user = getattr(context, "user", None) or {}
        if names and not user.get("encrypted_password"):
            return ToolOutcome(ok=False, summary="", error="No user connection is available.")
        from app.modules.intelligence.semantic_views import semantic_view_service

        available_views = (
            await semantic_view_service.list_active_for_agent(user) if names else []
        )
        selected_views = []
        for requested in names:
            matches = [
                view for view in available_views
                if requested.strip() in {str(view.get("id")), str(view.get("name"))}
            ]
            if len(matches) != 1:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=f"Published Semantic View {requested!r} is unavailable or ambiguous.",
                )
            selected_views.append(matches[0])
        view_ids = list(dict.fromkeys(str(view["id"]) for view in selected_views))

        database_name = getattr(context, "database", None) or (
            selected_views[0].get("database_name") if selected_views else None
        )

        sample_questions = invocation.arguments.get("sample_questions")
        questions = (
            [str(q) for q in sample_questions if str(q).strip()]
            if isinstance(sample_questions, list)
            else []
        )

        fields = {
            "name": name,
            "description": str(invocation.arguments.get("description") or "").strip(),
            "database_name": database_name,
            "schema_name": None,
            "instructions_response": str(
                invocation.arguments.get("instructions_response") or ""
            ).strip(),
            "instructions_orchestration": str(
                invocation.arguments.get("instructions_orchestration") or ""
            ).strip(),
            "response_style": "concise",
            "sample_questions": questions,
            "budget_seconds": 90,
            "tool_not_accessible": "accept",
            "default_tools": selected,
            "default_skills": [],
            "policy": "auto_read_only",
            "semantic_view_ids": view_ids,
            "visibility": "private",
        }

        existing = next(
            (
                a
                for a in await agent_repository.list_agents(owner_name=owner)
                if a["name"] == name
            ),
            None,
        )
        if existing is not None:
            return ToolOutcome(
                ok=False,
                summary="",
                error=f"Agent {name!r} already exists. Review it before requesting an update.",
            )
        audit_fields = {
            "event_type": "assistant_agent_create",
            "user_name": owner,
            "action": "CREATE",
            "object_type": "AGENT",
            "object_name": name,
            "session_id": getattr(context, "audit_session_id", None),
        }
        await write_audit_log(**audit_fields, status="PENDING")
        try:
            created = await agent_repository.create_agent(owner_name=owner, fields=fields)
        except Exception:
            await write_audit_log(**audit_fields, status="FAILED")
            return ToolOutcome(ok=False, summary="", error="The agent could not be created.")
        await write_audit_log(**audit_fields, status="SUCCESS")
        assert created is not None
        return ToolOutcome(
            ok=True,
            summary=(
                f"Created agent `{created['name']}` with tools: {', '.join(selected)}"
                + (f", Semantic Views: {', '.join(names)}" if names else "")
                + ". Open AI & ML > Agent to review it, or Nova Studio to chat with it. "
                "Do not create it again."
            ),
        )


create_agent_tool = CreateAgentTool()
