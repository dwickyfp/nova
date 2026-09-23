"""Consent-gated runtime wrapper for an administrator-registered MCP tool."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.common.audit import write_audit_log
from app.modules.agents import mcp_client
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.redaction import is_credential_column, is_credential_value

logger = logging.getLogger(__name__)


def _credential_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            is_credential_column(str(key)) or _credential_key(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_credential_key(item) for item in value)
    return False


class McpToolRunner:
    requires_consent = True
    classification: ToolClassification = "destructive"

    def __init__(self, server: dict[str, Any], tool: dict[str, Any]) -> None:
        self.server = server
        self.tool = tool
        self.name = "mcp_" + str(tool["tool_id"]).replace("-", "")
        self.description = (
            f"External MCP tool {tool['name']} on {server['name']}. "
            + str(tool.get("description") or "")[:1000]
        )
        self.parameters = tool.get("input_schema") or {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def preview(self, invocation: ToolInvocation) -> str:
        return f"Call external tool {self.tool['name']} on {self.server['name']}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        args = invocation.arguments or {}
        try:
            encoded = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            return ToolOutcome(ok=False, summary="", error="Invalid MCP arguments.")
        if (
            len(encoded.encode()) > 16_384
            or contains_credential_shape(encoded)
            or _credential_key(args)
        ):
            return ToolOutcome(ok=False, summary="", error="MCP arguments contain sensitive data.")
        user = getattr(context, "user", None) or {}
        username = str(user.get("username") or "")
        if not username:
            return ToolOutcome(ok=False, summary="", error="No authorized user is available.")
        try:
            result = await mcp_client.call_tool(self.server, str(self.tool["name"]), args)
            if result.get("isError"):
                raise mcp_client.McpError("The external tool reported an error.")
            content = result.get("content") or []
            if not isinstance(content, list):
                raise mcp_client.McpError("The external tool returned invalid content.")
            parts = [
                str(item.get("text") or "")
                for item in content[:20]
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if not parts and isinstance(result.get("structuredContent"), dict):
                parts = [json.dumps(result["structuredContent"], ensure_ascii=False)]
            rendered = "\n".join(parts)[:4000]
            safe = "[redacted]" if is_credential_value(rendered) else rendered
            outcome = ToolOutcome(
                ok=True,
                summary=f"External tool returned: {safe}" if safe else "External tool completed.",
                data={"content": safe},
                evidence={"source": "external_mcp", "server_id": self.server["server_id"]},
            )
            status = "SUCCESS"
        except mcp_client.McpError as exc:
            logger.warning("MCP invocation failed: %s", type(exc).__name__)
            outcome = ToolOutcome(ok=False, summary="", error="The external tool failed.")
            status = "FAILED"
        await write_audit_log(
            event_type="MCP_TOOL_CALL",
            user_name=username,
            action="CALL",
            object_type="MCP_TOOL",
            object_name=str(self.tool["tool_id"]),
            status=status,
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        return outcome
