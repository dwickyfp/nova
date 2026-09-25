"""Runtime-only coordination tools for a specialist child run."""

from __future__ import annotations

from typing import Any

from app.modules.agents.harness_repository import HarnessRepository
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome


class SendAgentMessageTool:
    name = "send_agent_message"
    description = (
        "Send a bounded finding or question to Auto while your analysis continues. "
        "Use this for material intermediate evidence; your final answer still goes to Auto."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message_type": {"type": "string", "enum": ["finding", "question"]},
            "content": {"type": "string", "maxLength": 4000},
        },
        "required": ["message_type", "content"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def __init__(self, repository: HarnessRepository, child: dict, root: dict) -> None:
        self.repository = repository
        self.child = child
        self.root = root

    def preview(self, invocation: ToolInvocation) -> str:
        return "Send a coordination message to Auto"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        args = invocation.arguments
        try:
            message_id = await self.repository.send(
                sender=self.child,
                recipient=self.root,
                operation_id=invocation.tool_call_id,
                message_type=str(args.get("message_type") or "finding"),
                content=str(args.get("content") or ""),
            )
        except ValueError:
            return ToolOutcome(ok=False, summary="", error="The coordination message was invalid.")
        return ToolOutcome(ok=True, summary="Sent to Auto", data={"message_id": message_id})


class RequestSpecialistTool(SendAgentMessageTool):
    name = "request_specialist"
    description = "Ask Auto to find another capability when your evidence requires it."
    parameters = {
        "type": "object",
        "properties": {
            "capability": {"type": "string", "maxLength": 200},
            "reason": {"type": "string", "maxLength": 1000},
        },
        "required": ["capability", "reason"],
        "additionalProperties": False,
    }

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        args = invocation.arguments
        capability = str(args.get("capability") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not capability or not reason:
            return ToolOutcome(ok=False, summary="", error="Capability and reason are required.")
        try:
            message_id = await self.repository.send(
                sender=self.child,
                recipient=self.root,
                operation_id=invocation.tool_call_id,
                message_type="question",
                content=f"Specialist requested for {capability[:200]}: {reason[:1000]}",
            )
        except ValueError:
            return ToolOutcome(ok=False, summary="", error="The request was invalid.")
        return ToolOutcome(
            ok=True, summary="Asked Auto for a specialist", data={"message_id": message_id}
        )
