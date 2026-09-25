"""Owner-scoped Agent Studio configuration inspection for Nove."""

from __future__ import annotations

from typing import Any

from app.common.audit import write_audit_log
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.agents.repository import agent_repository
from app.modules.assistant.app_context import _safe_text
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.intelligence.semantic_views import semantic_view_service

_AGENT_ENTITY_TYPES = frozenset({"agent", "studio_agent", "studio-agent"})


def _target_id(invocation: ToolInvocation, context: Any) -> str | None:
    supplied = invocation.arguments.get("agent_id")
    if supplied is not None:
        return supplied if isinstance(supplied, str) and 1 <= len(supplied) <= 128 else None
    app = getattr(context, "app_context", None)
    entity = getattr(app, "entity", None)
    if getattr(entity, "type", None) not in _AGENT_ENTITY_TYPES:
        return None
    entity_id = getattr(entity, "id", None)
    return entity_id if isinstance(entity_id, str) and 1 <= len(entity_id) <= 128 else None


def _names(value: Any, *, limit: int = 32) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_safe_text(item[:128]) for item in value[:limit] if isinstance(item, str)]


class InspectAgentConfigurationTool:
    name = "inspect_agent_configuration"
    description = (
        "Inspect the current Agent Studio agent's saved configuration under the "
        "signed-in owner's account. Returns provider/model names, Semantic View "
        "bindings, enabled tools and skills, and chart-tool configuration. "
        "Does not read the agent's conversations, instructions, secrets, or test data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "description": "Agent id. Omit to inspect the agent open in Studio.",
            }
        },
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        agent_id = invocation.arguments.get("agent_id")
        if isinstance(agent_id, str):
            safe_id = _safe_text(agent_id[:128]).replace("\n", " ").replace("\r", " ")
            return f"Inspect Agent Studio configuration {safe_id}"
        return "Inspect the current Agent Studio configuration"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        if getattr(context, "agent_id", None) is not None:
            return ToolOutcome(
                ok=False,
                summary="",
                error="This Nove capability is unavailable inside a Studio agent run.",
                error_class="POLICY_VIOLATION",
            )
        user = getattr(context, "user", None)
        try:
            if not isinstance(user, dict):
                raise SecurityContextError("No authenticated Nova session is available.")
            security = SecurityContext.from_session(user)
            if security.principal != getattr(context, "user_name", None):
                raise SecurityContextError("The Nova session does not match this conversation.")
        except SecurityContextError as exc:
            return ToolOutcome(
                ok=False,
                summary="",
                error=str(exc),
                error_class="AUTHORIZATION_FAILURE",
            )
        agent_id = _target_id(invocation, context)
        if agent_id is None:
            return ToolOutcome(
                ok=False,
                summary="",
                error="Open an Agent Studio configuration or provide an agent id.",
            )
        audit_fields = {
            "event_type": "assistant_agent_inspect",
            "user_name": security.principal,
            "action": "INSPECT",
            "object_type": "AGENT",
            "object_name": agent_id,
            "session_id": getattr(context, "audit_session_id", None),
            "active_role": security.active_role,
            "security_context_version": security.security_context_version,
        }
        try:
            agent = await agent_repository.get_agent(agent_id, owner_name=security.principal)
        except Exception:
            await write_audit_log(**audit_fields, status="FAILED")
            return ToolOutcome(ok=False, summary="", error="Agent configuration is unavailable.")
        if agent is None:
            await write_audit_log(**audit_fields, status="DENIED")
            return ToolOutcome(
                ok=False,
                summary="",
                error="Agent configuration is unavailable in your account.",
                error_class="AUTHORIZATION_FAILURE",
            )
        saved_view_ids = agent.get("semantic_view_ids")
        view_ids = _names(
            saved_view_ids if isinstance(saved_view_ids, list)
            else agent.get("semantic_model_ids"),
            limit=16,
        )
        view_names: dict[str, str] = {}
        view_names_available = True
        if view_ids:
            try:
                for view_id in view_ids:
                    view = await semantic_view_service.get_active_for_agent(
                        view_id, user, agent_id=agent_id
                    )
                    if isinstance(view, dict):
                        view_names[view_id] = _safe_text(
                            str(view.get("name") or "")[:128]
                        )
            except Exception:
                view_names_available = False
        tools = _names(agent.get("default_tools"))
        configured_chart_tool = "data_to_chart" in tools
        data = {
            "agent_id": agent_id,
            "name": _safe_text(str(agent.get("name") or "")[:256]),
            "provider_id": _safe_text(str(agent.get("model_provider_id") or "")[:128]),
            "model_name": _safe_text(str(agent.get("model_name") or "")[:128]),
            "semantic_views": [
                {"id": view_id, "name": view_names.get(view_id)} for view_id in view_ids
            ],
            "semantic_view_names_available": view_names_available,
            "enabled_tools": tools,
            "enabled_skills": _names(agent.get("default_skills"), limit=16),
            "discoverable_skills": _names(agent.get("discoverable_skills"), limit=16),
            "policy": _safe_text(str(agent.get("policy") or "")[:80]),
            "budget_seconds": agent.get("budget_seconds"),
            "budget_tokens": agent.get("budget_tokens"),
            "chart_tool_configured": configured_chart_tool,
            "diagnostics": (
                []
                if configured_chart_tool
                else ["The data_to_chart tool is not enabled for this agent."]
            ),
        }
        audit_id = await write_audit_log(**audit_fields, status="SUCCESS")
        return ToolOutcome(
            ok=True,
            summary=f"Inspected Agent Studio configuration {data['name'] or agent_id}.",
            data=data,
            evidence={"source": "owner_scoped_agent_configuration", "audit_id": audit_id},
        )


inspect_agent_configuration_tool = InspectAgentConfigurationTool()
