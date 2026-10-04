"""Read-only agent tools for published Semantic Views and Feature Groups."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.modules.agents.semantic.time_ranges import RANGE_GRAMMAR_HELP
from app.modules.assistant.evidence_health import (
    EvidenceFacts,
    assess_evidence,
    attach_evidence_health,
    attach_execution_health,
)
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
            "time": {
                "type": "object",
                "properties": {
                    "range": {"type": "string", "description": RANGE_GRAMMAR_HELP},
                    "grain": {"type": "string",
                              "enum": ["day", "week", "month", "quarter", "year"]},
                    "compare": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "order_by": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["field"],
                    "additionalProperties": False,
                },
            },
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
        return attach_execution_health(await self._run(invocation, context), self.name)

    async def _run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
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
                time=invocation.arguments.get("time"),
                order_by=invocation.arguments.get("order_by", []),
                version=invocation.arguments.get("version"),
                limit=min(int(invocation.arguments.get("limit", 20)), 100),
            )
        except (ValidationError, ValueError, TypeError):
            return ToolOutcome(ok=False, summary="", error="Invalid semantic query")
        expected_fingerprint = None
        manifest = getattr(context, "release_manifest", None)
        if manifest is not None:
            try:
                if not getattr(context, "agent_id", None):
                    raise ValueError("Pinned execution requires an agent")
                pins = [
                    pin for pin in manifest["dependencies"]["semantic_views"]
                    if pin["view_id"] == view_id
                ]
                if len(pins) != 1:
                    raise ValueError("Exactly one release binding is required")
                pin = pins[0]
                identity = EvidenceFacts(
                    semantic_view_id=view_id, semantic_version=pin["version"],
                    semantic_fingerprint=pin["fingerprint"],
                )
                if identity.semantic_fingerprint is None or identity.semantic_version is None:
                    raise ValueError("Release binding is incomplete")
                requested = invocation.arguments.get("version")
                if requested is not None and (
                    type(requested) is not int or requested != identity.semantic_version
                ):
                    raise ValueError("Requested version differs from the release")
                request = request.model_copy(update={"version": identity.semantic_version})
                expected_fingerprint = identity.semantic_fingerprint
            except (KeyError, TypeError, ValueError):
                return ToolOutcome(
                    ok=False, summary="", error="Semantic View does not match the pinned release",
                    error_class="POLICY_VIOLATION",
                )
        try:
            pin_options = (
                {"expected_fingerprint": expected_fingerprint}
                if expected_fingerprint is not None else {}
            )
            result = await semantic_view_service.query(
                view_id, request, user, agent_id=getattr(context, "agent_id", None), **pin_options,
            )
        except Exception as exc:
            logger.warning("semantic_view_query failed: %s", type(exc).__name__)
            return ToolOutcome(
                ok=False, summary="", error="Semantic View unavailable or unauthorized"
            )
        if expected_fingerprint is not None and (
            result.get("version") != request.version
            or result.get("model_fingerprint") != expected_fingerprint
        ):
            return ToolOutcome(
                ok=False, summary="", error="Semantic View does not match the pinned release",
                error_class="POLICY_VIOLATION",
            )
        columns = result["columns"][:100]
        rows = redact_rows(columns, [list(row)[:len(columns)] for row in result["rows"][:100]])
        truncated = (
            result.get("truncated") is True
            or len(result["rows"]) > 100 or len(result["columns"]) > 100
        )
        table = {"title": "Semantic View result", "columns": columns, "rows": rows}
        if truncated:
            table["truncated"] = True
        outcome = ToolOutcome(
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
        from app.core.config import settings

        if settings.STUDIO_BUSINESS_WORKFLOW_ENABLED:
            try:
                facts = EvidenceFacts(
                    semantic_grounding="published", semantic_view_id=view_id,
                    semantic_version=result["version"],
                    semantic_fingerprint=result.get("model_fingerprint"),
                    plan_source="compiled", verified_query_hit=False,
                    execution_status="success",
                    coverage=(
                        "truncated" if truncated else "complete"
                        if result.get("truncated") is False else "unknown"
                    ),
                    semantic_ambiguity="none", unsupported_numeric_claims=False,
                )
            except ValidationError:
                return outcome
            return attach_evidence_health(
                outcome, assess_evidence(facts, assessed_at=datetime.now(UTC)),
            )
        return outcome


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
        return attach_execution_health(await self._run(invocation, context), self.name)

    async def _run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
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
        expected_version = None
        manifest = getattr(context, "release_manifest", None)
        if manifest is not None:
            try:
                pins = [
                    pin for pin in manifest["dependencies"]["resources"]
                    if pin["kind"] == "feature_group" and pin["id"] == group
                ]
                if len(pins) != 1:
                    raise ValueError("Exactly one release binding is required")
                expected_version = pins[0]["version"]
                if type(expected_version) is not int or expected_version < 1:
                    raise ValueError("Release version is incomplete")
                requested = invocation.arguments.get("version")
                if requested is not None and (
                    type(requested) is not int or requested != expected_version
                ):
                    raise ValueError("Requested version differs from the release")
                request = request.model_copy(update={"version": expected_version})
            except (KeyError, TypeError, ValueError):
                return ToolOutcome(
                    ok=False, summary="", error="Feature Group does not match the pinned release",
                    error_class="RELEASE_DRIFT",
                )
        try:
            result = await feature_store.lookup(group, request, user)
        except Exception as exc:
            logger.warning("feature_lookup failed: %s", type(exc).__name__)
            return ToolOutcome(ok=False, summary="", error="Features unavailable or unauthorized")
        if expected_version is not None and (
            type(result.get("version")) is not int or result["version"] != expected_version
        ):
            return ToolOutcome(
                ok=False, summary="", error="Feature Group does not match the pinned release",
                error_class="RELEASE_DRIFT",
            )
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
                **({"fields_truncated": True} if len(result["values"]) > 100 else {}),
            },
            evidence={"source": "nova_feature_store", "group": group, "version": result["version"]},
        )


semantic_view_query_tool = SemanticViewQueryTool()
feature_lookup_tool = FeatureLookupTool()
