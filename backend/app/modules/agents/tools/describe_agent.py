"""Business metadata for the current agent, without database discovery."""

from __future__ import annotations

from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import redact_sql_credentials
from app.modules.agents.semantic.access import (
    _context_ids,
    load_authorized_models,
    load_specialist_models,
)
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry
from app.modules.assistant.tools.redaction import redact_row

DESCRIPTION = (
    "Describe this Studio agent's configured business data and capabilities. "
    "Use for 'data apa saja yang kamu punya', available metrics, sources, or what "
    "you can help with. Reads authorized Semantic View metadata and enabled tools "
    "and skills; never scans databases or executes business queries. In Smart, "
    "returns the combined Semantic Views of accessible specialists, attributed to "
    "their owning agents, without spawning them."
)
PARAMETERS = {
    "type": "object",
    "properties": {"offset": {"type": "integer", "minimum": 0}},
    "additionalProperties": False,
}
PAGE_SIZE = 4
FIELD_LIMIT = 24
SPECIALIST_LIMIT = 32


def _text(value: Any, limit: int = 240) -> str:
    return str(redact_row(["metadata"], [redact_sql_credentials(str(value or ""))])[0])[:limit]


def semantic_catalog(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for record in models:
        model = record["_scoped_ir"]
        metrics = [metric for metric in model.metrics if metric.visibility == "public"]
        dimensions = [field for dataset in model.datasets for field in dataset.fields
                      if field.kind.value == "dimension"]
        result.append({
            "view_id": _text(record.get("semantic_model_id"), 128),
            "name": _text(record.get("name") or model.name, 128),
            "version": _text(record.get("version") or model.version, 64),
            "description": _text(model.description),
            "metrics": [{"name": _text(metric.name, 128),
                         "description": _text(metric.description),
                         "unit": _text(metric.unit, 64)} for metric in metrics[:FIELD_LIMIT]],
            "dimensions": [{"name": _text(field.name, 128),
                            "description": _text(field.description)}
                           for field in dimensions[:FIELD_LIMIT]],
            "fields_truncated": len(metrics) > FIELD_LIMIT or len(dimensions) > FIELD_LIMIT,
            "shared_dimensions": _shared_dimensions(model),
        })
    return result


def _shared_dimensions(model: Any) -> list[list[str]]:
    """Dimensions that mean the same thing across facts ("sales_channel" and
    "marketing_channel"), so metrics from both can be asked for in one question."""
    return [
        [_text(field.rsplit(".", 1)[-1], 128) for field in group]
        for group in getattr(model, "conformed_dimensions", ())[:FIELD_LIMIT]
    ]


async def collaboration_catalog(context: Any) -> dict[str, Any]:
    """Combine every accessible specialist's Views, each read as that specialist runs it."""
    cached = getattr(context, "collaboration_catalog", None)
    if cached is not None:
        return cached
    from fastapi import HTTPException

    from app.modules.agents.auto_planner import authorized_candidates

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    specialists = []
    unavailable = 0
    for candidate in await authorized_candidates(context.user or {}):
        owner = {"agent_id": _text(candidate.agent_id, 128), "name": _text(candidate.name, 128)}
        views: list[dict[str, Any]] = []
        status = "ok"
        try:
            models, bound = await load_specialist_models(candidate, context)
            views = semantic_catalog(models)
            unavailable += max(0, bound - len(views))
        except HTTPException:
            # A drifted or unauthorized release pin hides that specialist only.
            status = "unavailable"
            unavailable += len(candidate.view_ids)
        for view in views:
            merged.setdefault((view["view_id"], view["version"]), {**view, "agents": []})[
                "agents"
            ].append(owner)
        specialists.append({
            **owner,
            "description": _text(candidate.manifest.delegation_description),
            "owns": [_text(item, 128) for item in candidate.manifest.owns[:FIELD_LIMIT]],
            "view_count": len(views),
            "catalog_status": status,
        })
    catalog = {
        "views": sorted(
            merged.values(),
            key=lambda view: (view["name"].casefold(), view["view_id"], view["version"]),
        ),
        "specialists": specialists,
        "unavailable": unavailable,
    }
    context.collaboration_catalog = catalog
    return catalog


def _names(specialists: list[dict[str, Any]]) -> str:
    names = [item["name"] for item in specialists if item["view_count"]]
    if len(names) > 4:
        return ", ".join(names[:4]) + f" and {len(names) - 4} more"
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return "".join(names)


class DescribeAgentTool:
    name = "describe_agent"
    description = DESCRIPTION
    parameters = PARAMETERS
    classification = "read_only"
    requires_consent = False

    def __init__(
        self, registry: ToolRegistry, *, name: str = "", resources: dict | None = None
    ) -> None:
        self.registry = registry
        self.agent_name = name
        self.resources = resources or {}

    def preview(self, invocation: ToolInvocation) -> str:
        return "Read this agent's business catalog"

    def planning_scope(self, context: Any) -> dict[str, Any]:
        smart = bool(getattr(context, "collaboration_root", False))
        views = semantic_catalog(getattr(context, "authorized_semantic_models", None) or [])
        if smart:
            return {
                "surface": "studio",
                "name": _text(self.agent_name, 128),
                "smart": True,
                "catalog_scope": "accessible_specialists",
                "catalog_tool": self.name,
                "free_form_sql": False,
                "resource_details_available_with": self.name,
            }
        return {
            "surface": "studio",
            "name": _text(self.agent_name, 128),
            "smart": False,
            "semantic_views": [{
                "name": view["name"], "description": view["description"],
                "metrics": [metric["name"] for metric in view["metrics"][:8]],
                "dimensions": [field["name"] for field in view["dimensions"][:8]],
                **({"shared_dimensions": view["shared_dimensions"]}
                   if view["shared_dimensions"] else {}),
                "details_available_with": self.name,
            } for view in views[:PAGE_SIZE]],
            "views_truncated": len(views) > PAGE_SIZE,
            "catalog_tool": self.name,
            "free_form_sql": False,
            "resource_details_available_with": self.name,
        }

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        if not getattr(context, "agent_id", None):
            return ToolOutcome(ok=False, summary="", error="No Studio agent is active.",
                               error_class="POLICY_VIOLATION")
        offset = invocation.arguments.get("offset", 0)
        if type(offset) is not int or offset < 0 or set(invocation.arguments) - {"offset"}:
            return ToolOutcome(ok=False, summary="", error="Invalid catalog page.")
        audit = {
            "event_type": "agent_catalog_read", "action": "INSPECT",
            "object_type": "AGENT", "object_name": context.agent_id,
            "user_name": context.user_name,
            "active_role": getattr(context, "role", None),
            "session_id": getattr(context, "audit_session_id", None),
        }
        try:
            from app.modules.agents.resources import authorized_resources

            resources = await authorized_resources(self.resources, {
                **(context.user or {}), "active_role": getattr(context, "role", None)
                or (context.user or {}).get("active_role"),
            })
            unavailable_bindings = 0
            specialists: list[dict[str, Any]] | None = None
            if getattr(context, "collaboration_root", False):
                catalog = await collaboration_catalog(context)
                views = catalog["views"]
                specialists = catalog["specialists"]
                unavailable_bindings = catalog["unavailable"]
            else:
                views = semantic_catalog(await load_authorized_models(context))
                unavailable_bindings = max(0, len(_context_ids(context)) - len(views))
            entries = views[offset:offset + PAGE_SIZE]
            total = len(views)
        except Exception:
            await write_audit_log(**audit, status="FAILED")
            return ToolOutcome(ok=False, summary="", error="Agent catalog is unavailable.",
                               error_class="CATALOG_UNAVAILABLE")
        tools = self.registry.names()
        skills = list(dict.fromkeys((*self.registry.default_skills,
                                    *self.registry.discoverable_skills)))
        data = {
            "name": _text(self.agent_name, 128),
            "semantic_views": entries,
            "total": total,
            "unavailable_binding_count": unavailable_bindings,
            "next_offset": offset + PAGE_SIZE if offset + PAGE_SIZE < total else None,
            "tools": [{"name": _text(name, 128),
                       "description": _text(getattr(self.registry.get(name), "description", ""))}
                      for name in tools[:32]],
            "skills": [_text(name, 128) for name in skills[:32]],
            "capabilities_truncated": len(tools) > 32 or len(skills) > 32,
            "evidence_kind": "agent_catalog",
            "free_form_sql": False,
            "resources": resources,
            "limitations": (
                "Metadata describes configured capabilities, not current values, row counts, "
                "freshness, or date coverage. Tool availability alone does not establish "
                "which external data sources exist. Do not infer unlisted sources."
            ),
        }
        summary = "Read the authorized business catalog for this agent."
        if specialists is not None:
            data["specialists"] = specialists[:SPECIALIST_LIMIT]
            data["specialists_truncated"] = len(specialists) > SPECIALIST_LIMIT
            owners = _names(specialists)
            plural = "" if total == 1 else "s"
            summary = (f"Read {total} Semantic View{plural} across {owners}." if owners
                       else "No specialist Semantic Views are available.")
        audit_id = await write_audit_log(**audit, status="SUCCESS")
        return ToolOutcome(
            ok=True, summary=summary, data=data,
            evidence={"source": "agent_configuration", "audit_id": audit_id},
            metadata={"evidence_kind": "agent_catalog"},
        )
