"""``schedule_automation``: turn a question into a recurring report from chat.

"Jadikan laporan mingguan tiap Senin jam 8" becomes a stored automation for the
current Studio agent. Creating one changes persistent configuration, so the
call is classified as a write and always asks the user; the automation itself
then runs read-only as its owner (see ``automations``).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 256},
        "prompt": {"type": "string", "description": "The question each run asks the agent."},
        "schedule_kind": {"type": "string", "enum": ["cron", "interval"]},
        "schedule_expr": {
            "type": "string",
            "description": "Cron such as '0 8 * * 1' (Mondays 08:00), or "
            "'EVERY(INTERVAL 1 DAY)'.",
        },
        "timezone": {"type": "string", "description": "IANA zone, e.g. Asia/Jakarta."},
        "condition": {
            "type": "object",
            "description": "Deliver only when a result metric crosses a threshold.",
            "properties": {
                "metric": {"type": "string"},
                "operator": {"type": "string", "enum": [">", ">=", "<", "<=", "=", "!="]},
                "value": {"type": "number"},
            },
            "required": ["metric", "operator", "value"],
            "additionalProperties": False,
        },
    },
    "required": ["title", "prompt", "schedule_kind", "schedule_expr"],
    "additionalProperties": False,
}


class ScheduleAutomationTool:
    name = "schedule_automation"
    description = (
        "Schedule this agent to answer a question on a recurring basis (a weekly report, "
        "a daily alert when a metric crosses a threshold). Results arrive in Studio history. "
        "Use only when the user asks for a recurring report or alert."
    )
    parameters = PARAMETERS
    #: A persistent configuration change: always approved by the user.
    classification: ToolClassification = "destructive"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        return (
            f"Schedule '{str(args.get('title', ''))[:80]}' "
            f"({args.get('schedule_kind', '')}: {str(args.get('schedule_expr', ''))[:60]})"
        )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.agents.automations import (
            AutomationCreate,
            AutomationError,
            automation_repository,
        )

        agent_id = getattr(context, "agent_id", None)
        owner = getattr(context, "agent_owner_name", None)
        user = getattr(context, "user", None) or {}
        if not agent_id or not owner or user.get("username") != owner:
            return ToolOutcome(
                ok=False, summary="", error="Only the agent owner can schedule its automations.",
                error_class="PERMISSION_DENIED",
            )
        try:
            body = AutomationCreate(**(invocation.arguments or {}))
            created = await automation_repository.create(
                agent_id=agent_id, owner_name=owner,
                role_name=str(getattr(context, "role", None) or user.get("active_role") or ""),
                body=body,
            )
        except (ValidationError, AutomationError) as exc:
            return ToolOutcome(
                ok=False, summary="", error=str(exc)[:300], error_class="INVALID_TOOL_ARGUMENTS",
                recoverable=True, safe_detail=str(exc)[:300],
            )
        return ToolOutcome(
            ok=True,
            summary=(
                f"Scheduled '{created['title']}'. Next run: {created['next_run_at']} UTC. "
                "Each run appears in Studio history."
            ),
            data={"automation_id": created["automation_id"],
                  "next_run_at": str(created["next_run_at"])},
            metadata={"evidence_kind": "action"},
        )


schedule_automation_tool = ScheduleAutomationTool()
