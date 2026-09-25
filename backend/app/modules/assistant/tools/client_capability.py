"""Dispatch a surface-owned client action without granting DOM or script access."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.common.audit import write_audit_log
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

_ARGUMENTS: dict[str, dict[str, type]] = {
    "surface.refresh": {},
    "surface.set_filter": {"filter": str, "value": str},
    "surface.select": {"id": str},
    "tab.open": {"tab": str},
    "editor.focus": {},
}


def _valid_arguments(capability: str, args: Any) -> bool:
    fields = _ARGUMENTS.get(capability)
    if fields is None or not isinstance(args, dict) or set(args) != set(fields):
        return False
    return all(
        isinstance(args[name], kind)
        and bool(args[name].strip())
        and len(args[name]) <= 128
        for name, kind in fields.items()
    )


class InvokeClientCapabilityTool:
    name = "invoke_client_capability"
    description = (
        "Request a safe capability registered by the current Nova application "
        "surface, such as changing a filter, opening a tab, refreshing, or "
        "focusing the editor. The action is pending until a matching application "
        "outcome event arrives. Never claim it completed from this tool result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "capability": {"type": "string", "enum": sorted(_ARGUMENTS)},
            "args": {"type": "object", "additionalProperties": True},
        },
        "required": ["capability", "args"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        capability = str(invocation.arguments.get("capability") or "")[:80]
        return f"Request {capability} on the current surface"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        app = getattr(context, "app_context", None)
        if app is None:
            return ToolOutcome(
                ok=False, summary="", error="No active application surface is available."
            )
        capability = invocation.arguments.get("capability")
        args = invocation.arguments.get("args")
        if not isinstance(capability, str) or capability not in app.capabilities:
            return ToolOutcome(
                ok=False, summary="", error="That capability is unavailable on the current surface."
            )
        if not _valid_arguments(capability, args):
            return ToolOutcome(ok=False, summary="", error="Invalid client capability arguments.")
        audit_id = None
        if isinstance(getattr(context, "user", None), dict):
            try:
                audit_id = await write_audit_log(
                    event_type="assistant_client_action",
                    user_name=context.user_name,
                    action=capability,
                    object_type="NOVA_SURFACE",
                    object_name=app.surface.id,
                    status="PENDING",
                    session_id=getattr(context, "audit_session_id", None),
                    active_role=getattr(context, "role", None),
                )
            except Exception:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error="The page action could not be recorded for audit.",
                    error_class="AUDIT_UNAVAILABLE",
                )
        action = {
            "capability": capability,
            "args": args,
            "correlation_id": str(uuid4()),
            "surface_id": app.surface.id,
        }
        return ToolOutcome(
            ok=True,
            summary=f"Requested {capability}; awaiting the application outcome.",
            data={
                "status": "pending",
                "capability": capability,
                "correlation_id": action["correlation_id"],
            },
            evidence={"audit_id": audit_id} if audit_id else {},
            metadata={"client_action": action},
        )


invoke_client_capability_tool = InvokeClientCapabilityTool()
