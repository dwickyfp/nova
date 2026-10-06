"""``propose_automation``: Smart drafts a recurring report or alert; the user creates it.

A schedule changes persistent configuration, so it needs the user's consent, and
a Smart turn runs unattended and cannot ask. The tool therefore writes nothing:
it validates the schedule and returns a proposal, which Studio shows as a card
whose button creates the automation through the automation endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.modules.agents.tools.schedule_automation import PARAMETERS
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

DESCRIPTION = (
    "Propose a recurring report or a threshold alert when the user asks for one "
    "(a weekly report, 'tell me when expense passes 5 billion'). It creates nothing: "
    "the user sees the proposal and confirms it. Write the prompt as the question each "
    "run should answer, in the user's words."
)


class ProposeAutomationTool:
    name = "propose_automation"
    description = DESCRIPTION
    parameters = PARAMETERS
    classification: ToolClassification = "read_only"
    #: Nothing is written; creating the automation is the user's own action.
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        return f"Propose a schedule: {invocation.arguments.get('title', '')}"[:200]

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        from app.modules.agents.automations import AutomationCreate, AutomationError, next_run

        try:
            body = AutomationCreate(**invocation.arguments)
            upcoming = next_run(
                body.schedule_kind, body.schedule_expr, body.timezone, datetime.now(UTC)
            )
        except (ValidationError, AutomationError, TypeError) as exc:
            detail = str(exc) if isinstance(exc, AutomationError) else "Invalid schedule."
            return ToolOutcome(
                ok=False, summary="", error=detail, error_class="INVALID_TOOL_ARGUMENTS",
                recoverable=True, safe_detail=detail,
            )
        proposal = {
            **body.model_dump(mode="json", exclude={"delivery", "enabled"}),
            "next_run": upcoming.isoformat(),
        }
        if hasattr(context, "automation_proposal"):
            context.automation_proposal = proposal
        return ToolOutcome(
            ok=True,
            summary=f"Proposed '{body.title}'; it starts once the user confirms it.",
            data={"proposal": proposal, "created": False},
            metadata={"evidence_kind": "automation_proposal"},
        )


propose_automation_tool = ProposeAutomationTool()
