"""Add a verified Studio result to a Dataviz dashboard (one pipeline for all).

The Agent card's **Add to Dataviz**, the Dataviz assistant's apply step and
``dashboard_build`` all publish through :func:`publish`
(docs/arch-19-studio-dataviz.md, D3). It is a saga, not a fake transaction:
Superset and PostgreSQL are separate systems, so every step is journaled
in ``CONFIG_DATAVIZ_PUBLICATIONS``, a failure removes exactly what this
operation created and nothing else uses, and an outcome that cannot be proven
either way is left ``NEEDS_RECONCILIATION`` for its owner.

Every Superset call runs as the caller's own BI session. Nothing here uses a
service identity, and the chart's query is proven through Superset's chart
data API, as the caller, before anything is attached.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from app.common.audit import write_audit_log
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.core.metadata_scope import acting_as, actor_for
from app.modules.agents.artifact_repository import artifact_repository, validate_artifact_sql

from . import capability as capabilities
from .charts import ChartPlan, to_superset
from .client import BiError, SupersetClient
from .metadata_repository import superset_metadata
from .previews import Locator, PreviewError, ResolvedPreview, fingerprint, resolve, verify

logger = logging.getLogger(__name__)

Status = Literal[
    "VALIDATING",
    "DATASET_READY",
    "CHART_READY",
    "ATTACHING",
    "SUCCEEDED",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
    "COMPENSATING",
    "NEEDS_RECONCILIATION",
]
_LOCK_MS = 60_000
_CHART_WIDTH = 6
_CHART_HEIGHT = 50
_TITLE = re.compile(r"^[^\x00-\x1f]{1,250}$")


class PublishError(Exception):
    """A publication was refused or failed; ``status`` is the HTTP status."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str,
        capability: dict | None = None,
        operation: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.capability = capability
        self.operation = operation


@dataclass
class Target:
    mode: Literal["existing", "new"]
    dashboard_id: int | None = None
    title: str | None = None
    #: The revision the caller previewed; a different current one is a conflict.
    expected_revision: str | None = None


@dataclass
class Operation:
    operation_id: str
    owner_name: str
    idempotency_scope: str
    request_fingerprint: str
    status: Status = "VALIDATING"
    target_mode: str | None = None
    dashboard_id: int | None = None
    chart_id: int | None = None
    dataset_id: int | None = None
    artifact_id: str | None = None
    created: list[str] = field(default_factory=list)
    query_fingerprint: str | None = None
    visualization_fingerprint: str | None = None
    source: Locator | None = None
    strategy_id: str | None = None
    revision_before: str | None = None
    revision_after: str | None = None
    business_context: dict | None = None
    active_role: str | None = None
    security_context_version: int | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: _now())

    _storage_status: str | None = field(default=None, repr=False)
    _storage_updated_at: datetime | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            "dashboard_id": self.dashboard_id,
            "chart_id": self.chart_id,
            "dataset_id": self.dataset_id,
            "artifact_id": self.artifact_id,
            "bi_view_path": dashboard_path(self.dashboard_id) if self.dashboard_id else None,
            "revision_after": self.revision_after,
            "reused_chart": "chart" not in self.created and self.chart_id is not None,
            "concepts": [
                {"qualified_name": item.get("qualified_name"), "label": item.get("label")}
                for item in (self.business_context or {}).get("concepts") or []
            ],
            "error": self.error,
        }


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def dashboard_path(dashboard_id: int | None) -> str:
    return f"/bi/superset/dashboard/{int(dashboard_id or 0)}/"


# ── Journal ─────────────────────────────────────────────────────────────────


async def ensure_schema() -> None:
    from app.core.metadata_runtime import metadata_db

    await metadata_db().probe()


async def _save(op: Operation) -> None:
    source = op.source
    async with superset_metadata() as repository:
        actor = repository.actor
        if (op.owner_name, op.active_role, op.security_context_version) != (
            actor.principal,
            actor.active_role,
            actor.security_context_version,
        ):
            raise MetadataPermissionDenied()
        previous = op._storage_status
        updated_at = op._storage_updated_at
        if previous is None:
            row, created = await repository.start_publication(
                {
                    "operation_id": op.operation_id,
                    "idempotency_scope": op.idempotency_scope,
                    "request_fingerprint": op.request_fingerprint,
                    "target_mode": op.target_mode,
                    "source_thread_id": source.thread_id if source else None,
                    "source_message_id": source.message_id if source else None,
                    "source_content_id": source.content_id if source else None,
                }
            )
            if not created:
                raise MetadataConflict()
            previous, updated_at = row["status"], row["updated_at"]
        row = await repository.advance_publication(
            op.operation_id,
            previous,
            {
                "status": op.status,
                "target_mode": op.target_mode,
                "dashboard_id": op.dashboard_id,
                "chart_id": op.chart_id,
                "dataset_id": op.dataset_id,
                "artifact_id": op.artifact_id,
                "created_objects": ",".join(op.created) or None,
                "query_fingerprint": op.query_fingerprint,
                "visualization_fingerprint": op.visualization_fingerprint,
                "strategy_id": op.strategy_id,
                "revision_before": op.revision_before,
                "revision_after": op.revision_after,
                "business_context": op.business_context,
                "error": (op.error or "")[:1000] or None,
            },
            expected_updated_at=updated_at,
        )
    op._storage_status, op._storage_updated_at = row["status"], row["updated_at"]
    op.created_at = row["created_at"]


def _from_row(data: dict[str, Any]) -> Operation:
    business = data.get("business_context")
    if isinstance(business, str):
        try:
            business = json.loads(business)
        except ValueError:
            business = None
    source = None
    if data.get("source_thread_id"):
        source = Locator(
            data["source_thread_id"], data["source_message_id"], data["source_content_id"]
        )
    return Operation(
        operation_id=data["operation_id"],
        owner_name=data["owner_name"],
        idempotency_scope=data["idempotency_scope"],
        request_fingerprint=data["request_fingerprint"],
        status=data["status"],
        target_mode=data.get("target_mode"),
        dashboard_id=data.get("dashboard_id"),
        chart_id=data.get("chart_id"),
        dataset_id=data.get("dataset_id"),
        artifact_id=data.get("artifact_id"),
        created=[item for item in str(data.get("created_objects") or "").split(",") if item],
        query_fingerprint=data.get("query_fingerprint"),
        visualization_fingerprint=data.get("visualization_fingerprint"),
        source=source,
        strategy_id=data.get("strategy_id"),
        revision_before=data.get("revision_before"),
        revision_after=data.get("revision_after"),
        business_context=business,
        active_role=data.get("active_role"),
        security_context_version=data.get("security_context_version"),
        error=data.get("error"),
        created_at=data.get("created_at") or _now(),
        _storage_status=data["status"],
        _storage_updated_at=data["updated_at"],
    )


async def _by_scope(scope: str, owner: str) -> Operation | None:
    async with superset_metadata(read_only=True) as repository:
        if owner != repository.actor.principal:
            raise MetadataPermissionDenied()
        row = await repository.publication_by_scope(scope)
    return _from_row(row) if row else None


async def get_operation(operation_id: str, owner: str) -> Operation | None:
    async with superset_metadata(read_only=True) as repository:
        if owner != repository.actor.principal:
            raise MetadataPermissionDenied()
        row = await repository.get_publication(operation_id)
    return _from_row(row) if row else None


async def list_operations(
    owner: str,
    *,
    unresolved: bool = False,
    limit: int = 50,
) -> list[Operation]:
    async with superset_metadata(read_only=True) as repository:
        if owner != repository.actor.principal:
            raise MetadataPermissionDenied()
        rows = await repository.list_publications(unresolved=unresolved, limit=limit)
    return [_from_row(row) for row in rows]


async def _reusable_chart(owner: str, query_fp: str, viz_fp: str) -> Operation | None:
    async with superset_metadata(read_only=True) as repository:
        if owner != repository.actor.principal:
            raise MetadataPermissionDenied()
        row = await repository.reusable_chart(query_fp, viz_fp)
    return _from_row(row) if row else None


# ── Coordination ────────────────────────────────────────────────────────────


def _redis():
    from app.core.redis import session_store

    if session_store._redis is None:
        raise PublishError(
            "Dataviz publishing is unavailable right now.",
            status=503,
            code="COORDINATION_UNAVAILABLE",
        )
    return session_store._redis


class _Lock:
    """A short Redis lease. It only orders Nova writers; it is not the record."""

    def __init__(self, key: str, holder: str) -> None:
        self.key, self.holder = key, holder

    async def __aenter__(self) -> _Lock:
        redis = _redis()
        for _ in range(100):
            if await redis.set(self.key, self.holder, nx=True, px=_LOCK_MS):
                return self
            await asyncio.sleep(0.1)
        raise PublishError(
            "Another change to this dashboard is still running. Try again.",
            status=409,
            code="DASHBOARD_BUSY",
        )

    async def __aexit__(self, *exc: object) -> None:
        redis = _redis()
        try:
            if (await redis.get(self.key)) in (self.holder, self.holder.encode()):
                await redis.delete(self.key)
        except Exception:  # noqa: BLE001 - the lease expires on its own
            logger.warning("Releasing %s failed", self.key)


# ── Superset helpers ────────────────────────────────────────────────────────


def revision(dashboard: dict[str, Any]) -> str:
    """A dashboard's revision: its change time, layout and metadata."""
    return fingerprint(
        dashboard.get("changed_on_utc") or dashboard.get("changed_on"),
        dashboard.get("position_json") or "",
        dashboard.get("json_metadata") or "",
    )


async def read_dashboard(bi: SupersetClient, dashboard_id: int) -> dict[str, Any]:
    return (await bi.request("GET", f"/api/v1/dashboard/{int(dashboard_id)}")).get("result") or {}


async def me(bi: SupersetClient) -> dict[str, Any]:
    return (await bi.request("GET", "/api/v1/me/")).get("result") or {}


def can_edit(dashboard: dict[str, Any], current: dict[str, Any], superset_role: str) -> bool:
    from .access import SUPERSET_ADMIN

    if superset_role == SUPERSET_ADMIN:
        return True
    owners = {
        int(owner.get("id"))
        for owner in dashboard.get("owners") or []
        if owner.get("id") is not None
    }
    return current.get("id") is not None and int(current["id"]) in owners


async def editable_dashboards(
    user: dict[str, Any], *, search: str = "", page: int = 0, page_size: int = 20
) -> dict[str, Any]:
    """Dashboards the caller may add a chart to, newest first.

    Only dashboards the caller's BI session can see and edit are listed; an id
    the caller cannot edit is never shown, so nothing can be probed.
    """
    from .access import superset_role_for

    superset_role = await superset_role_for(
        str(user.get("active_role") or ""), actor=actor_for(user)
    )
    text = re.sub(r"[^\w \-]", " ", search)[:80].strip()
    query = (
        (f"(filters:!((col:dashboard_title,opr:ct,value:'{text}'))," if text else "(")
        + "order_column:changed_on_delta_humanized,order_direction:desc,"
        + f"page:{max(0, page)},page_size:{max(1, min(page_size, 50))})"
    )
    async with SupersetClient.for_user(user, author=False) as bi:
        current = await me(bi)
        body = await bi.request("GET", f"/api/v1/dashboard/?q={query}")
    items = []
    for row in body.get("result") or []:
        if can_edit(row, current, superset_role):
            items.append(
                {
                    "id": row["id"],
                    "title": row.get("dashboard_title") or f"Dashboard {row['id']}",
                    "published": bool(row.get("published")),
                    "own": any(
                        int(o.get("id", -1)) == int(current.get("id", -2))
                        for o in row.get("owners") or []
                    ),
                    "changed": row.get("changed_on_delta_humanized"),
                    "bi_view_path": dashboard_path(row["id"]),
                }
            )
    count = int(body.get("count") or 0)
    return {"dashboards": items, "page": page, "has_more": (page + 1) * page_size < count}


def _chart_description(preview: ResolvedPreview) -> str | None:
    """The business meaning carried onto the Superset chart, from the ontology."""
    business = preview.business_context
    concepts = (business or {}).get("concepts") or []
    if not concepts:
        return None
    lines = []
    for item in concepts[:6]:
        name = item.get("label") or item.get("qualified_name")
        definition = str(item.get("definition") or "").strip()
        lines.append(f"{name} ({item.get('qualified_name')}): {definition}"[:400])
    release = (business or {}).get("ontology_release_id")
    lines.append(f"Supernova Business Ontology release {release}.")
    return "\n".join(lines)[:2000]


def append_chart(
    layout: dict[str, Any] | None, chart_id: int, name: str, title: str
) -> tuple[dict[str, Any], str]:
    """``layout`` plus one chart in a new row at the end; every node kept.

    Returns the layout and a human description of where the chart went. A
    chart already on the dashboard leaves it unchanged.
    """
    layout = json.loads(json.dumps(layout or {}))
    for node in layout.values():
        if (
            isinstance(node, dict)
            and node.get("type") == "CHART"
            and (node.get("meta") or {}).get("chartId") == chart_id
        ):
            return layout, "already on the dashboard"
    if "ROOT_ID" not in layout:
        layout = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
            "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
            "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
            **{k: v for k, v in layout.items() if k not in ("ROOT_ID", "GRID_ID", "HEADER_ID")},
        }
    root_children = layout["ROOT_ID"].get("children") or []
    parent_id = "GRID_ID"
    where = "a new row at the bottom"
    if root_children and str(root_children[0]).startswith("TABS"):
        tabs = layout.get(root_children[0]) or {}
        first_tab = (tabs.get("children") or [None])[0]
        if first_tab and first_tab in layout:
            parent_id = first_tab
            tab_title = ((layout[first_tab].get("meta") or {}).get("text")) or "the first tab"
            where = f"a new row at the bottom of tab “{tab_title}”"
    if parent_id not in layout:
        layout[parent_id] = {
            "type": "GRID",
            "id": parent_id,
            "children": [],
            "parents": ["ROOT_ID"],
        }
        layout["ROOT_ID"].setdefault("children", []).append(parent_id)
    parent = layout[parent_id]
    parents = [*(parent.get("parents") or []), parent_id]
    suffix = uuid4().hex[:8]
    row_id, node_id = f"ROW-nova-{suffix}", f"CHART-nova-{suffix}"
    parent.setdefault("children", []).append(row_id)
    layout[row_id] = {
        "type": "ROW",
        "id": row_id,
        "children": [node_id],
        "parents": parents,
        "meta": {"background": "BACKGROUND_TRANSPARENT"},
    }
    layout[node_id] = {
        "type": "CHART",
        "id": node_id,
        "children": [],
        "parents": [*parents, row_id],
        "meta": {
            "chartId": chart_id,
            "width": _CHART_WIDTH,
            "height": _CHART_HEIGHT,
            "sliceName": name,
        },
    }
    return layout, where


def _chart_ids(layout: dict[str, Any]) -> set[int]:
    return {
        int((node.get("meta") or {})["chartId"])
        for node in layout.values()
        if isinstance(node, dict)
        and node.get("type") == "CHART"
        and isinstance((node.get("meta") or {}).get("chartId"), int)
    }


async def _delete_quietly(bi: SupersetClient, path: str) -> bool:
    try:
        await bi.request("DELETE", path)
        return True
    except BiError as exc:
        logger.warning("Dataviz compensation of %s failed: %s", path, exc.message)
        return False


# ── Preview and diff ────────────────────────────────────────────────────────


@dataclass
class Plan:
    preview: ResolvedPreview
    capability: capabilities.CapabilityResult
    plan: ChartPlan
    strategy_id: str | None
    title: str


async def plan_for(
    ref: str,
    user: dict[str, Any],
    *,
    approved_transform_id: str | None = None,
    title: str | None = None,
) -> Plan:
    """Resolve a signed reference and decide what would be published."""
    try:
        preview = await resolve(verify(ref, user), user)
    except PreviewError as exc:
        raise PublishError(str(exc), status=404, code="PREVIEW_NOT_FOUND") from exc
    result = await capabilities.check(preview)
    if result.status == "SUPPORTED" and result.plan is not None and not approved_transform_id:
        plan, strategy = result.plan, None
    elif approved_transform_id and approved_transform_id in result.plans:
        plan, strategy = result.plans[approved_transform_id], approved_transform_id
    else:
        status = 503 if result.reason_code == "PROVIDER_UNAVAILABLE" else 422
        raise PublishError(
            result.message, status=status, code=result.reason_code, capability=result.public()
        )
    clean = (title or preview.title or "Chart").strip()[:250] or "Chart"
    return Plan(preview, result, plan, strategy, clean)


async def describe_change(user: dict[str, Any], plan: Plan, target: Target) -> dict[str, Any]:
    """A human diff of what :func:`publish` would do, without changing anything."""
    async with SupersetClient.for_user(user) as bi:
        if target.mode == "new":
            return {
                "target": {"mode": "new", "title": target.title},
                "revision": None,
                "changes": [
                    f"Create the dashboard “{target.title}”.",
                    f"Add the chart “{plan.title}” ({plan.plan.kind}).",
                    "Your role becomes the dashboard's owner; viewers see only their own "
                    "governed rows.",
                ],
                "charts_before": 0,
            }
        dashboard = await read_dashboard(bi, int(target.dashboard_id or 0))
    layout = json.loads(dashboard.get("position_json") or "{}")
    _, where = append_chart(layout, -1, plan.title, dashboard.get("dashboard_title") or "")
    filters = (
        json.loads(dashboard.get("json_metadata") or "{}").get("native_filter_configuration") or []
    )
    changes = [
        f"Add the chart “{plan.title}” ({plan.plan.kind}) in {where} of "
        f"“{dashboard.get('dashboard_title')}”.",
        f"Keep its {len(_chart_ids(layout))} existing charts and {len(filters)} filters "
        "as they are.",
    ]
    if plan.strategy_id:
        lost = next(
            (a.lost for a in plan.capability.alternatives if a.strategy_id == plan.strategy_id), []
        )
        changes.append("You approved this form; it does not keep " + ", ".join(lost) + ".")
    return {
        "target": {
            "mode": "existing",
            "dashboard_id": dashboard.get("id"),
            "title": dashboard.get("dashboard_title"),
        },
        "revision": revision(dashboard),
        "changes": changes,
        "charts_before": len(_chart_ids(layout)),
    }


# ── The saga ────────────────────────────────────────────────────────────────


async def _audit(
    user: dict[str, Any], action: str, op: Operation, status: str, reason: str | None = None
) -> None:
    """Audit one publication step with its ontology concepts (redacted ids only).

    ``AUDIT_LOG.decision`` is 32 characters, so the operation, chart and
    concepts go in ``object_name``. The journal row is the durable record of
    the outcome: an audit write failure is logged and never turns a verified
    change into an error the user would retry.
    """
    concepts = ",".join(
        str(item.get("qualified_name"))
        for item in (op.business_context or {}).get("concepts") or []
    )
    detail = (
        f"dashboard={op.dashboard_id};chart={op.chart_id};op={op.operation_id};"
        f"concepts={concepts or '-'}"
    )
    try:
        await write_audit_log(
            event_type="DATAVIZ",
            user_name=user["username"],
            action=action,
            object_type="SUPERSET_DASHBOARD" if op.dashboard_id else "DATAVIZ_PUBLICATION",
            object_name=detail[:500],
            status=status,
            decision=(op.strategy_id or "direct")[:32],
            error_message=reason,
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
        )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("Dataviz audit write failed for %s", op.operation_id)


async def _dataset(bi: SupersetClient, preview: ResolvedPreview, sql: str, op: Operation) -> int:
    """Reuse the owner's dataset over the same statement, or create one."""
    name = f"nova_pub_{(preview.query_fingerprint() or '')[7:19]}"
    query = f"(filters:!((col:table_name,opr:eq,value:'{name}')),page_size:5)"
    found = (await bi.request("GET", f"/api/v1/dataset/?q={query}")).get("result") or []
    current = await me(bi)
    for row in found:
        owners = {int(o.get("id", -1)) for o in row.get("owners") or []}
        if int(current.get("id", -2)) in owners or not owners:
            detail = (await bi.request("GET", f"/api/v1/dataset/{row['id']}")).get("result") or {}
            if " ".join(str(detail.get("sql") or "").split()) == " ".join(sql.split()):
                return int(row["id"])
    body: dict[str, Any] = {
        "database": await bi.nova_database_id(),
        "catalog": "default_catalog",
        "table_name": name,
        "sql": sql,
    }
    created = await bi.request("POST", "/api/v1/dataset/", body=body)
    op.created.append("dataset")
    return int(created["id"])


async def _columns(bi: SupersetClient, dataset_id: int) -> tuple[list[str], set[str]]:
    body = await bi.request("GET", f"/api/v1/dataset/{dataset_id}")
    columns = body.get("result", {}).get("columns", [])
    return (
        [str(c["column_name"]) for c in columns],
        {str(c["column_name"]) for c in columns if c.get("is_dttm")},
    )


async def _proven(
    bi: SupersetClient, plan: ChartPlan, dataset_id: int, preview: ResolvedPreview
) -> tuple[str, dict, dict]:
    """Strict form data (no fallback) whose query runs as the caller.

    For an axis or pie chart drawn from rows, the native view drew one mark per
    row; Superset adds rows up per category. Before saving, the category's row
    count is checked so the published chart cannot silently aggregate.
    """
    columns, temporal = await _columns(bi, dataset_id)
    try:
        viz_type, form, query_context = to_superset(
            plan, dataset_id=dataset_id, columns=columns, temporal=temporal
        )
    except ValueError as exc:
        raise PublishError(str(exc), status=422, code="FIELDS_NOT_IN_DATASET") from exc
    await bi.request("POST", "/api/v1/chart/data", body=query_context)
    if (
        preview.locator.node is not None
        and plan.kind not in ("table", "big_number", "scatter")
        and plan.x
    ):
        keys = [plan.x, *([plan.series] if plan.series else [])]
        check = {
            "datasource": {"id": dataset_id, "type": "table"},
            "force": False,
            "queries": [
                {
                    "columns": keys,
                    "metrics": ["count"],
                    "orderby": [["count", False]],
                    "row_limit": 1,
                    "filters": [],
                    "extras": {},
                }
            ],
            "result_format": "json",
            "result_type": "full",
        }
        rows = ((await bi.request("POST", "/api/v1/chart/data", body=check)).get("result") or [{}])[
            0
        ].get("data") or []
        if rows and int(rows[0].get("count") or 0) > 1:
            raise PublishError(
                f"The source now has several rows per {plan.x}; Dataviz would add them up.",
                status=422,
                code="NON_UNIQUE_CATEGORY",
            )
    return viz_type, form, query_context


async def publish(
    user: dict[str, Any],
    *,
    ref: str,
    idempotency_key: str,
    target: Target,
    approved_transform_id: str | None = None,
    title: str | None = None,
    editor_state: Literal["clean", "dirty", "unknown"] = "unknown",
    open_dashboard_id: int | None = None,
    agent_id: str | None = None,
) -> Operation:
    """Publish one verified preview onto one dashboard. See the module docs."""
    async with acting_as(actor_for(user)):
        if not re.fullmatch(r"[A-Za-z0-9_\-:.]{8,128}", idempotency_key or ""):
            raise PublishError(
                "A publish request needs an idempotency key.", status=422, code="BAD_REQUEST"
            )
        if target.mode == "new" and not (target.title and _TITLE.match(target.title.strip())):
            raise PublishError("Name the new dashboard.", status=422, code="BAD_REQUEST")
        if target.mode == "existing" and not target.dashboard_id:
            raise PublishError("Choose a dashboard.", status=422, code="BAD_REQUEST")
        if (
            target.mode == "existing"
            and editor_state == "dirty"
            and open_dashboard_id == target.dashboard_id
        ):
            raise PublishError(
                "This dashboard has unsaved changes in Dataviz. Save or discard them first, so "
                "nothing you changed is overwritten.",
                status=409,
                code="EDITOR_HAS_UNSAVED_CHANGES",
            )
        if not await capabilities.may_publish(user):
            await write_audit_log(
                event_type="DATAVIZ",
                user_name=user["username"],
                action="PUBLISH_CHART",
                object_type="DATAVIZ_PUBLICATION",
                object_name=str(target.dashboard_id or "new"),
                status="DENIED",
                error_message="active role is not a BI author",
                session_id=user.get("session_id"),
                active_role=user.get("active_role"),
                security_context_version=user.get("security_context_version"),
            )
            raise PublishError(
                "Your active role can view dashboards but cannot add charts. Nothing was changed.",
                status=403,
                code="NOT_AUTHOR",
            )

        request_fp = fingerprint(
            ref,
            target.mode,
            target.dashboard_id,
            (target.title or "").strip(),
            approved_transform_id,
            (title or "").strip(),
        )
        scope = fingerprint(
            user["username"],
            user.get("active_role"),
            user.get("security_context_version"),
            idempotency_key,
        )
        redis_key = f"nova:dataviz:publish:{scope[7:39]}"
        async with _Lock(redis_key, uuid4().hex):
            previous = await _by_scope(scope, user["username"])
            if previous is not None:
                if previous.request_fingerprint != request_fp:
                    raise PublishError(
                        "This request id was already used for a different change.",
                        status=409,
                        code="IDEMPOTENCY_CONFLICT",
                    )
                if previous.status in ("SUCCEEDED", "NEEDS_RECONCILIATION", "FAILED_FINAL"):
                    return previous
            plan = await plan_for(
                ref, user, approved_transform_id=approved_transform_id, title=title
            )
            preview = plan.preview
            # A dashboard-dataset answer is drawn on that dataset: it has no SQL.
            sql = "" if preview.dataset_source else validate_artifact_sql(preview.sql or "")
            op = previous or Operation(
                operation_id=f"pub_{uuid4().hex[:20]}",
                owner_name=user["username"],
                idempotency_scope=scope,
                request_fingerprint=request_fp,
            )
            op.status, op.error = "VALIDATING", None
            op.target_mode = target.mode
            op.dashboard_id = target.dashboard_id
            op.source = preview.locator
            op.strategy_id = plan.strategy_id
            op.query_fingerprint = preview.query_fingerprint()
            op.visualization_fingerprint = fingerprint(
                plan.plan.model_dump(mode="json"), plan.title
            )
            op.business_context = preview.business_context
            op.active_role = user.get("active_role")
            op.security_context_version = user.get("security_context_version")
            await _save(op)
            try:
                return await _run(user, op, plan, sql, target, agent_id=agent_id)
            except PublishError:
                raise
            except BiError as exc:
                op.status = (
                    "FAILED_FINAL" if exc.status in (401, 403, 404, 422) else "FAILED_RETRYABLE"
                )
                op.error = exc.message
                await _save(op)
                await _audit(user, "PUBLISH_CHART", op, "FAILED", exc.message)
                status = (
                    exc.status
                    if exc.status in (401, 403, 404)
                    else (503 if exc.status == 0 or exc.status >= 500 else 422)
                )
                raise PublishError(
                    exc.message, status=status, code="PROVIDER_ERROR", operation=op.public()
                ) from exc


async def _run(
    user: dict[str, Any],
    op: Operation,
    plan: Plan,
    sql: str,
    target: Target,
    *,
    agent_id: str | None,
) -> Operation:
    preview = plan.preview
    async with SupersetClient.for_user(user) as bi:
        from .access import superset_role_for

        superset_role = await superset_role_for(
            str(user.get("active_role") or ""), actor=actor_for(user)
        )
        current = await me(bi)
        dashboard: dict[str, Any] = {}
        if target.mode == "existing":
            try:
                dashboard = await read_dashboard(bi, int(target.dashboard_id or 0))
            except BiError as exc:
                if exc.status in (403, 404):
                    raise PublishError(
                        "That dashboard is not available to you.",
                        status=404,
                        code="DASHBOARD_NOT_FOUND",
                    ) from exc
                raise
            if not can_edit(dashboard, current, superset_role):
                op.status, op.error = "FAILED_FINAL", "not an owner of the dashboard"
                await _save(op)
                await _audit(user, "PUBLISH_CHART", op, "DENIED", op.error)
                raise PublishError(
                    "You can view this dashboard but cannot change it. Nothing was changed.",
                    status=403,
                    code="NOT_DASHBOARD_EDITOR",
                )
            op.revision_before = revision(dashboard)
            if target.expected_revision and target.expected_revision != op.revision_before:
                op.status, op.error = "FAILED_FINAL", "dashboard changed since the preview"
                await _save(op)
                raise PublishError(
                    "The dashboard changed since this was prepared. Review it again on the latest "
                    "version.",
                    status=409,
                    code="REVISION_CONFLICT",
                )

        # Chart: reuse the owner's identical publication when it still exists.
        reusable = await _reusable_chart(
            op.owner_name, op.query_fingerprint or "", op.visualization_fingerprint or ""
        )
        if reusable and reusable.chart_id and op.chart_id is None:
            try:
                await bi.request("GET", f"/api/v1/chart/{reusable.chart_id}")
                op.chart_id, op.dataset_id = int(reusable.chart_id), reusable.dataset_id
                op.artifact_id = reusable.artifact_id
            except BiError:
                op.chart_id = None
        if op.chart_id is None:
            if preview.dataset_source:
                # The open dashboard's own dataset, read as the caller; Nova
                # creates nothing and so removes nothing on failure.
                op.dataset_id = int(preview.dataset_source["dataset_id"])
            else:
                op.dataset_id = op.dataset_id or await _dataset(bi, preview, sql, op)
            op.status = "DATASET_READY"
            await _save(op)
            try:
                viz_type, form, query_context = await _proven(bi, plan.plan, op.dataset_id, preview)
                body: dict[str, Any] = {
                    "slice_name": plan.title,
                    "viz_type": viz_type,
                    "datasource_id": op.dataset_id,
                    "datasource_type": "table",
                    "params": json.dumps(form),
                    "query_context": json.dumps(query_context),
                }
                description = _chart_description(preview)
                if description:
                    body["description"] = description
                chart = await bi.request("POST", "/api/v1/chart/", body=body)
            except (BiError, PublishError) as exc:
                await _compensate(bi, op)
                op.status = "FAILED_FINAL" if isinstance(exc, PublishError) else "FAILED_RETRYABLE"
                op.error = getattr(exc, "message", str(exc))
                await _save(op)
                await _audit(user, "PUBLISH_CHART", op, "FAILED", op.error)
                raise
            op.chart_id = int(chart["id"])
            op.created.append("chart")
        op.status = "CHART_READY"
        await _save(op)

        # Attach under the dashboard lease: re-read, merge, write, read back.
        lock_key = f"nova:dataviz:dashboard:{target.dashboard_id or 'new-' + op.operation_id}"
        async with _Lock(lock_key, op.operation_id):
            op.status = "ATTACHING"
            await _save(op)
            try:
                if target.mode == "new":
                    layout, _ = append_chart(None, op.chart_id, plan.title, target.title or "")
                    layout["HEADER_ID"]["meta"]["text"] = target.title
                    created = await bi.request(
                        "POST",
                        "/api/v1/dashboard/",
                        body={
                            "dashboard_title": (target.title or "").strip(),
                            "position_json": json.dumps(layout),
                            # Superset attaches it to the caller's Nova role
                            # and publishes it to that role (D8).
                        },
                    )
                    op.dashboard_id = int(created["id"])
                    op.created.append("dashboard")
                else:
                    latest = await read_dashboard(bi, int(op.dashboard_id or 0))
                    if revision(latest) != op.revision_before:
                        raise PublishError(
                            "Someone changed this dashboard while the chart was prepared. Nothing "
                            "was overwritten; add it again on the latest version.",
                            status=409,
                            code="REVISION_CONFLICT",
                        )
                    before = json.loads(latest.get("position_json") or "{}")
                    layout, _ = append_chart(
                        before, op.chart_id, plan.title, latest.get("dashboard_title") or ""
                    )
                    if not _chart_ids(before) <= _chart_ids(layout):
                        raise PublishError(
                            "The layout merge would drop a chart.",
                            status=500,
                            code="MERGE_INVARIANT",
                        )
                    if layout != before:
                        await bi.request(
                            "PUT",
                            f"/api/v1/dashboard/{op.dashboard_id}",
                            body={"position_json": json.dumps(layout)},
                        )
                chart = (await bi.request("GET", f"/api/v1/chart/{op.chart_id}")).get(
                    "result"
                ) or {}
                memberships = {
                    int(d["id"]) for d in chart.get("dashboards") or [] if d.get("id") is not None
                }
                dashboard_id = int(op.dashboard_id or 0)
                if dashboard_id not in memberships:
                    await bi.request(
                        "PUT",
                        f"/api/v1/chart/{op.chart_id}",
                        body={"dashboards": sorted(memberships | {dashboard_id})},
                    )
                after = await read_dashboard(bi, dashboard_id)
                placed = op.chart_id in _chart_ids(json.loads(after.get("position_json") or "{}"))
                member_rows = (
                    await bi.request("GET", f"/api/v1/dashboard/{op.dashboard_id}/charts")
                ).get("result") or []
                if not placed or op.chart_id not in {int(r["id"]) for r in member_rows}:
                    raise BiError("The dashboard did not keep the chart.", status=500)
                op.revision_after = revision(after)
            except PublishError as exc:
                await _compensate(bi, op)
                op.status, op.error = "FAILED_FINAL", exc.message
                await _save(op)
                await _audit(user, "PUBLISH_CHART", op, "FAILED", exc.message)
                raise
            except BiError as exc:
                op.status, op.error = "NEEDS_RECONCILIATION", exc.message
                await _save(op)
                await _audit(user, "PUBLISH_CHART", op, "FAILED", exc.message)
                raise PublishError(
                    "The chart was created but placing it could not be confirmed. It is listed for "
                    "review; nothing else on the dashboard was removed.",
                    status=503,
                    code="NEEDS_RECONCILIATION",
                    operation=op.public(),
                ) from exc

    # A chart drawn on a dashboard's own dataset has no SQL of its own, so it
    # gets no Studio artifact record: Superset holds it, and it is reached
    # through its dashboard.
    if op.artifact_id is None and not preview.dataset_source:
        artifact = await artifact_repository.create_bi(
            owner_name=user["username"],
            title=plan.title,
            artifact_type="chart",
            sql_text=sql,
            database_name=None,
            schema_name=None,
            bi_kind="chart",
            bi_object_id=int(op.chart_id),
            bi_dataset_id=op.dataset_id,
            agent_id=agent_id or preview.agent_id,
            thread_id=preview.locator.thread_id,
        )
        op.artifact_id = artifact["artifact_id"]
    if "dashboard" in op.created and op.dashboard_id:
        # A dashboard this operation created is listed in Studio and can be
        # shared like any dashboard; Superset still holds its definition.
        await artifact_repository.create_bi(
            owner_name=user["username"],
            title=(target.title or "Dashboard").strip()[:256],
            artifact_type="dashboard",
            sql_text="",
            database_name=None,
            schema_name=None,
            bi_kind="dashboard",
            bi_object_id=int(op.dashboard_id),
            bi_dataset_id=None,
            agent_id=agent_id or preview.agent_id,
            thread_id=preview.locator.thread_id,
        )
    op.status, op.error = "SUCCEEDED", None
    await _save(op)
    await _audit(user, "PUBLISH_CHART", op, "SUCCESS")
    return op


async def _compensate(bi: SupersetClient, op: Operation) -> None:
    """Remove what this operation created and nothing else depends on."""
    op.status = "COMPENSATING"
    if "chart" in op.created and op.chart_id:
        chart: dict[str, Any] = {}
        with contextlib.suppress(BiError):
            chart = (await bi.request("GET", f"/api/v1/chart/{op.chart_id}")).get("result") or {}
        if not chart.get("dashboards") and await _delete_quietly(
            bi, f"/api/v1/chart/{op.chart_id}"
        ):
            op.created.remove("chart")
            op.chart_id = None
    if "dataset" in op.created and op.dataset_id:
        try:
            related = await bi.request("GET", f"/api/v1/dataset/{op.dataset_id}/related_objects")
            in_use = int((related.get("charts") or {}).get("count") or 0)
        except BiError:
            in_use = 1
        if not in_use and await _delete_quietly(bi, f"/api/v1/dataset/{op.dataset_id}"):
            op.created.remove("dataset")
            op.dataset_id = None
