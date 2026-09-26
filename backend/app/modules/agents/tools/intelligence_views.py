"""Read-only agent tools for published Semantic Views and Feature Groups."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.redaction import redact_rows
from app.modules.intelligence.feature_store import FeatureLookup, feature_store
from app.modules.intelligence.semantic_views import SemanticViewQuery, semantic_view_service

logger = logging.getLogger(__name__)


def _user(context: Any) -> dict | None:
    user = getattr(context, "user", None) or {}
    if not user.get("username") or not user.get("encrypted_password"):
        return None
    return {
        **user,
        "active_role": getattr(context, "role", None)
        or getattr(context, "active_role", None)
        or user.get("active_role"),
        "session_id": getattr(context, "audit_session_id", None) or user.get("session_id"),
    }


class SemanticViewQueryTool:
    name = "semantic_view_query"
    description = "Query a published Nova Semantic View using named metrics and dimensions."
    parameters = {
        "type": "object",
        "properties": {
            "view_id": {"type": "string"},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "dimensions": {"type": "array", "items": {"type": "string"}},
            "named_filters": {"type": "array", "items": {"type": "string"}},
            "filters": {"type": "object", "additionalProperties": {
                "anyOf": [
                    {"type": "string"}, {"type": "integer"},
                    {"type": "number"}, {"type": "boolean"},
                ],
            }},
            "version": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["view_id", "metrics"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        return f"semantic_view_query: {str(invocation.arguments.get('view_id', ''))[:64]}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = _user(context)
        if user is None:
            return ToolOutcome(ok=False, summary="", error="User connection unavailable")
        view_id = invocation.arguments.get("view_id")
        if not isinstance(view_id, str) or not view_id:
            return ToolOutcome(ok=False, summary="", error="Semantic View is required")
        if getattr(context, "agent_id", None):
            from app.modules.agents.semantic.access import _context_ids

            if view_id not in _context_ids(context):
                return ToolOutcome(
                    ok=False, summary="", error="Semantic View is not bound to this agent"
                )
        try:
            request = SemanticViewQuery(
                metrics=invocation.arguments.get("metrics", []),
                dimensions=invocation.arguments.get("dimensions", []),
                named_filters=invocation.arguments.get("named_filters", []),
                filters=invocation.arguments.get("filters", {}),
                version=invocation.arguments.get("version"),
                limit=min(int(invocation.arguments.get("limit", 20)), 100),
            )
        except (ValidationError, ValueError, TypeError):
            return ToolOutcome(ok=False, summary="", error="Invalid semantic query")
        try:
            result = await semantic_view_service.query(
                view_id, request, user, agent_id=getattr(context, "agent_id", None)
            )
        except Exception as exc:
            logger.warning("semantic_view_query failed: %s", type(exc).__name__)
            return ToolOutcome(
                ok=False, summary="", error="Semantic View unavailable or unauthorized"
            )
        columns = result["columns"][:100]
        rows = redact_rows(columns, result["rows"][:100])
        table = {"title": "Semantic View result", "columns": columns, "rows": rows}
        return ToolOutcome(
            ok=True,
            summary=f"{len(rows)} row(s) from Semantic View",
            table=table,
            data={
                "view_id": view_id,
                "version": result["version"],
                "columns": columns,
                "rows": rows,
            },
            evidence={
                "source": "nova_semantic_view",
                "view_id": view_id,
                "version": result["version"],
            },
        )


class FeatureLookupTool:
    def __init__(self, bindings: dict | None = None) -> None:
        self.groups = frozenset((bindings or {}).get("feature_groups") or [])

    name = "feature_lookup"
    description = "Look up a Nova Feature Group by its governed entity key."
    parameters = {
        "type": "object",
        "properties": {
            "group": {"type": "string"},
            "entity_key": {
                "type": "object",
                "additionalProperties": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                },
            },
            "version": {"type": "integer", "minimum": 1},
            "as_of": {"type": "string", "description": "Optional ISO 8601 point in time."},
        },
        "required": ["group", "entity_key"],
        "additionalProperties": False,
    }
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation: ToolInvocation) -> str:
        return f"feature_lookup: {str(invocation.arguments.get('group', ''))[:128]}"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = _user(context)
        if user is None:
            return ToolOutcome(ok=False, summary="", error="User connection unavailable")
        group = invocation.arguments.get("group")
        if not isinstance(group, str) or not group:
            return ToolOutcome(ok=False, summary="", error="Feature Group is required")
        if getattr(context, "agent_id", None) and group not in self.groups:
            return ToolOutcome(
                ok=False,
                summary="",
                error="Feature Group is not bound to this agent",
                error_class="POLICY_VIOLATION",
            )
        entity_key = invocation.arguments.get("entity_key")
        if not isinstance(entity_key, dict):
            return ToolOutcome(ok=False, summary="", error="Invalid entity key")
        try:
            request = FeatureLookup(
                entity_key=entity_key,
                version=invocation.arguments.get("version"),
                as_of=invocation.arguments.get("as_of"),
            )
        except ValidationError:
            return ToolOutcome(ok=False, summary="", error="Invalid entity key")
        try:
            result = await feature_store.lookup(group, request, user)
        except Exception as exc:
            logger.warning("feature_lookup failed: %s", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="Features unavailable or unauthorized")
        names = list(result["values"])[:100]
        values = redact_rows(names, [[result["values"][name] for name in names]])[0]
        safe = dict(zip(names, values, strict=True))
        return ToolOutcome(
            ok=True,
            summary=f"{len(safe)} feature(s) from {group}",
            data={
                "group": group,
                "version": result["version"],
                "values": safe,
                "as_of": result["as_of"],
            },
            evidence={"source": "nova_feature_store", "group": group, "version": result["version"]},
        )


semantic_view_query_tool = SemanticViewQueryTool()
feature_lookup_tool = FeatureLookupTool()
