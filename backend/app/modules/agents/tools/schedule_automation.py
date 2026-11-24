"""``schedule_automation``: turn a question into a recurring report from chat.

"Jadikan laporan mingguan tiap Senin jam 8" becomes a stored automation for the
current Studio agent. Creating one changes persistent configuration, so the
call is classified as a write and always asks the user; the automation itself
then runs read-only as its owner (see ``automations``).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

logger = logging.getLogger(__name__)

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
    #: A persistent configuration change. Studio makes it without asking (the
    #: loop audits it); an unattended run can never schedule another automation.
    classification: ToolClassification = "destructive"
    requires_consent = True
    #: What Nova says when the turn ends without this step succeeding.
    not_done_message = "loop.not_scheduled"

    def preview(self, invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        return (
            f"Schedule '{str(args.get('title', ''))[:80]}' "
            f"({args.get('schedule_kind', '')}: {str(args.get('schedule_expr', ''))[:60]})"
        )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.core.metadata_db import (
            MetadataConflict,
            MetadataOutcomeUnknown,
            MetadataPermissionDenied,
            MetadataReadOnlyMaintenance,
            MetadataUnavailable,
        )
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
        except ValidationError as exc:
            return _refused(validation_message(exc), "INVALID_TOOL_ARGUMENTS")
        try:
            created = await automation_repository.create(
                agent_id=agent_id, owner_name=owner,
                role_name=str(getattr(context, "role", None) or user.get("active_role") or ""),
                body=body,
            )
        except AutomationError as exc:
            return _refused(*_classified(exc))
        except MetadataPermissionDenied:
            return _refused("Your active role cannot schedule automations for this agent.",
                            "PERMISSION_DENIED", recoverable=False)
        except MetadataOutcomeUnknown:
            # The write may have committed: asking again could schedule it twice.
            return _refused(
                "Nova could not confirm whether the automation was saved. Check the "
                "agent's automations before scheduling it again.", "OUTCOME_UNKNOWN",
                recoverable=False)
        except (MetadataUnavailable, MetadataReadOnlyMaintenance, MetadataConflict):
            return _refused("Automations cannot be saved right now. Try again shortly.",
                            "TEMPORARILY_UNAVAILABLE", recoverable=False)
        timezone = str(created.get("timezone") or body.timezone)
        next_utc, next_local = _next_run_times(created.get("next_run_at"), timezone)
        return ToolOutcome(
            ok=True,
            summary=(
                f"Scheduled '{created['title']}'. Schedule time zone: {timezone}. "
                f"Next run: {next_local} ({timezone}), which is {next_utc} UTC. "
                "Each run appears in Studio history. State only this next run and this "
                "time zone; do not work out other dates or zones."
            ),
            data={"automation_id": created["automation_id"],
                  "next_run_at": next_utc, "next_run_local": next_local,
                  "timezone": timezone},
            metadata={"evidence_kind": "action"},
        )


def validation_message(exc: ValidationError) -> str:
    """The arguments a call got wrong, as ``field: problem`` lines.

    Pydantic's own text links to its documentation site; a reason that holds a
    link is shown to the user as a generic failure, and the agent cannot see
    what to fix. This names each field and what is wrong with it.
    """
    parts = []
    for error in exc.errors(include_url=False)[:4]:
        field = ".".join(str(part) for part in error.get("loc") or ()) or "arguments"
        message = str(error.get("msg") or "is invalid").rstrip(".")
        parts.append(f"{field}: {message}")
    return "; ".join(parts) + "."


def _classified(exc: Exception) -> tuple[str, str, bool]:
    """An automation store refusal as (message, error_class, recoverable)."""
    from app.modules.agents.automations import (
        MAX_AUTOMATIONS_PER_AGENT,
        AutomationConflict,
    )

    text = " ".join(str(exc).split())[:300]
    if isinstance(exc, AutomationConflict):
        return text, "CONFLICT", False
    if text.startswith(f"An agent has at most {MAX_AUTOMATIONS_PER_AGENT}"):
        return (f"{text} Remove an automation this agent no longer needs first.",
                "LIMIT_REACHED", False)
    if ("cron" in text or "interval" in text or "timezone" in text
            or "schedule" in text.casefold() or "Automations run at most" in text):
        return text, "INVALID_SCHEDULE", True
    return text, "INVALID_TOOL_ARGUMENTS", True


def _refused(message: str, error_class: str, recoverable: bool = True) -> ToolOutcome:
    # The reason, without arguments or identities, so a failed first attempt
    # can be traced to its cause in the server log.
    logger.info("schedule_automation refused: class=%s reason=%s", error_class, message[:300])
    return ToolOutcome(
        ok=False, summary="", error=message, error_class=error_class,
        recoverable=recoverable, safe_detail=message,
    )


def _next_run_times(value: Any, timezone: str) -> tuple[str, str]:
    """The scheduler's next run as ISO UTC and as wall time in the schedule's zone."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value, value
    if not isinstance(value, datetime):
        return str(value), str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    utc = value.astimezone(UTC)
    try:
        local = utc.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        local = utc
    return utc.isoformat(), local.strftime("%Y-%m-%d %H:%M")


schedule_automation_tool = ScheduleAutomationTool()
