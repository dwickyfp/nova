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
    "data_to_chart",
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
        "semantic_model_name": {
            "type": "string",
            "description": (
                "Name of one of the user's existing semantic models to bind. "
                "Omit if the agent should not use a semantic model."
            ),
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Tools to bundle. Valid: load_skill, semantic_query, "
                "semantic_search, query_execute, data_to_chart."
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
        "semantic model. Requires your approval before it writes."
    )
    parameters = _PARAMETERS
    classification: ToolClassification = "destructive"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        name = str(invocation.arguments.get("name") or "").strip()
        model = str(invocation.arguments.get("semantic_model_name") or "").strip()
        tools = invocation.arguments.get("tools") or []
        tool_list = ", ".join(str(t) for t in tools) if isinstance(tools, list) else ""
        suffix = f" (semantic model: {model})" if model else ""
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
            selected = [str(t) for t in tools if str(t) in _ALLOWED_TOOLS]
        else:
            selected = ["load_skill", "semantic_query", "data_to_chart"]
        if not selected:
            selected = ["load_skill"]

        semantic_model_id = None
        model_name = str(invocation.arguments.get("semantic_model_name") or "").strip()
        if model_name:
            models = await agent_repository.list_semantic_models(owner_name=owner)
            match = next((m for m in models if m["name"] == model_name), None)
            if match is None:
                available = ", ".join(m["name"] for m in models) or "none"
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=(f"No semantic model named {model_name!r}. Available: {available}."),
                )
            semantic_model_id = match["semantic_model_id"]

        database_name = getattr(context, "database", None) or None

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
            "semantic_model_id": semantic_model_id,
            "visibility": "private",
        }

        # Idempotent by name: update the caller's agent of this name rather than
        # creating a second one when a model retries the step.
        existing = next(
            (
                a
                for a in await agent_repository.list_agents(owner_name=owner)
                if a["name"] == name
            ),
            None,
        )
        if existing is not None:
            created = await agent_repository.update_agent(
                existing["agent_id"], owner_name=owner, fields=fields
            )
            verb = "Updated"
        else:
            created = await agent_repository.create_agent(owner_name=owner, fields=fields)
            verb = "Created"
        assert created is not None
        return ToolOutcome(
            ok=True,
            summary=(
                f"{verb} agent `{created['name']}` with tools: {', '.join(selected)}"
                + (f", semantic model: {model_name}" if model_name else "")
                + ". Open AI & ML > Agent to review it, or Nova Studio to chat with it. "
                "Do not create it again."
            ),
        )


create_agent_tool = CreateAgentTool()
