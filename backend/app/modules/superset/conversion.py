"""Convert Studio artifacts saved before BI into Superset objects (Phase 6).

A legacy artifact keeps its id: conversion creates the Superset dataset, chart
or dashboard as the owner and points the existing row at it, so threads,
favorites and dashboards that name the artifact still resolve.

Each artifact is analysed first. *Blockers* change what a chart computes
(calculated and level-of-detail fields, table calculations, aggregate or
relative-date filters, aggregations BI computes differently); an artifact with
any stays a read-only legacy artifact unless its owner converts it anyway
(``force``). *Notes* only change presentation (sort, size or label encodings,
dashboard filter zones) and never stop a conversion. Every outcome, with its
blockers and notes, is returned and audited.

Conversion runs as the owner: in-app with their own session, or for an owner
the worker account may impersonate through a delegated session
(``scripts/convert_studio_artifacts.py``).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.common.audit import write_audit_log
from app.modules.agents.artifact_repository import artifact_repository

from .artifacts import create_chart_objects, create_dashboard_object
from .charts import ChartPlan, plan_from_vega, worksheet_conversion
from .client import BiError, SupersetClient

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    artifact_id: str
    title: str
    artifact_type: str
    status: str  # converted | kept | failed
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    bi_view_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def chart_conversion(artifact: dict[str, Any]) -> tuple[ChartPlan | None, list[str], list[str]]:
    """``(plan, blockers, notes)`` for a legacy chart artifact."""
    viz_spec = artifact.get("viz_spec")
    if viz_spec:
        try:
            return worksheet_conversion(viz_spec)
        except ValueError as exc:
            return None, [f"an unreadable worksheet ({exc})"], []
    chart_spec = artifact.get("chart_spec")
    if chart_spec:
        plan = plan_from_vega(chart_spec)
        notes = [] if plan.kind != "table" else ["this chart type is drawn as a table"]
        return plan, [], notes
    return None, [], []


def dashboard_conversion(
    zones: list[dict[str, Any]],
    charts: dict[str, dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """``(chart artifact ids, text blocks, blockers, notes)`` for a dashboard."""
    placed: list[str] = []
    texts: list[str] = []
    unconverted: list[str] = []
    notes: list[str] = []
    for zone in sorted(zones, key=lambda z: (z["rect"]["y"], z["rect"]["x"])):
        kind = zone.get("kind")
        if kind == "sheet" and zone.get("artifact_id"):
            chart = charts.get(zone["artifact_id"])
            if chart is None or not chart.get("bi_object_id"):
                unconverted.append(chart["title"] if chart else zone["artifact_id"])
            else:
                placed.append(zone["artifact_id"])
        elif kind == "text" and zone.get("text"):
            texts.append(str(zone["text"].get("content", "")))
        elif kind == "filter":
            field_name = (zone.get("filter") or {}).get("field", "a field")
            notes.append(f"the dashboard filter on {field_name}; add a native filter in BI")
    blockers = [f"charts that are not converted ({', '.join(unconverted)})"] if unconverted else []
    if len({z["rect"]["w"] for z in zones if z.get("kind") == "sheet"}) > 1:
        notes.append("zone sizes are laid out two charts per row")
    if actions:
        notes.append("dashboard actions; BI cross-filters charts on the same dataset")
    return placed, texts, blockers, notes


async def _audit(user: dict[str, Any], outcome: Outcome) -> None:
    await write_audit_log(
        event_type="bi_artifact",
        user_name=user["username"],
        action="CONVERT_ARTIFACT",
        object_type=outcome.artifact_type.upper(),
        object_name=outcome.artifact_id,
        status={"converted": "SUCCESS", "kept": "SKIPPED"}.get(outcome.status, "FAILED"),
        error_message="; ".join(outcome.blockers) or outcome.error,
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )


async def convert_owner(
    user: dict[str, Any], *, artifact_ids: list[str] | None = None, force: bool = False
) -> list[Outcome]:
    """Convert ``user``'s legacy artifacts (all, or ``artifact_ids``) to BI.

    Charts go first so dashboards can place them. A refusal of the whole
    session (not signed in, not a BI author) is raised; anything else is
    reported per artifact.
    """
    from app.modules.viz.migration import legacy_tile_dashboards

    owner = user["username"]
    wanted = set(artifact_ids) if artifact_ids else None
    # Another process may have converted the last artifact and dropped the
    # legacy columns since this one checked the table.
    await artifact_repository.ensure_bi_columns()
    pending = await artifact_repository.list_legacy(owner_name=owner)
    if wanted is not None:
        # A dashboard converts together with the legacy charts it places.
        for row in pending:
            if row["artifact_id"] in wanted and row["artifact_type"] == "dashboard":
                wanted.update(_placed_chart_ids(row.get("viz_spec") or {}))
    legacy = [row for row in pending if wanted is None or row["artifact_id"] in wanted]
    tiles = [
        tile
        for tile in await legacy_tile_dashboards(owner)
        if wanted is None or tile["artifact_id"] in wanted
    ]
    outcomes: list[Outcome] = []
    if not legacy and not tiles:
        return outcomes

    async with SupersetClient.for_user(user) as bi:
        for artifact in [row for row in legacy if row["artifact_type"] == "chart"]:
            outcomes.append(await _convert_chart(bi, owner, artifact, force=force))

        charts = {
            row["artifact_id"]: row
            for row in await artifact_repository.list(owner_name=owner)
            if row["artifact_type"] == "chart"
        }
        dashboards = [
            (row["artifact_id"], row["title"], row.get("viz_spec") or {}, True)
            for row in legacy
            if row["artifact_type"] == "dashboard"
        ] + [
            (tile["artifact_id"], tile["title"], {"zones": tile["zones"]}, False) for tile in tiles
        ]
        for artifact_id, title, spec, exists in dashboards:
            outcomes.append(
                await _convert_dashboard(
                    bi, owner, artifact_id, title, spec, charts, exists=exists, force=force
                )
            )

    for outcome in outcomes:
        await _audit(user, outcome)
    if await artifact_repository.retire_legacy_columns():
        logger.info("Every Studio artifact is in BI; the legacy columns were dropped")
    return outcomes


async def _convert_chart(
    bi: SupersetClient, owner: str, artifact: dict[str, Any], *, force: bool
) -> Outcome:
    plan, blockers, notes = chart_conversion(artifact)
    outcome = Outcome(artifact["artifact_id"], artifact["title"], "chart", "kept", blockers, notes)
    if blockers and not force:
        return outcome
    try:
        dataset_id, chart_id = await create_chart_objects(
            bi,
            title=artifact["title"],
            sql=artifact["sql_text"],
            plan=plan,
            schema=artifact.get("schema_name") or artifact.get("database_name"),
        )
    except BiError as exc:
        if exc.status in (401, 403):
            raise
        return Outcome(
            outcome.artifact_id, outcome.title, "chart", "failed", blockers, notes, exc.message
        )
    converted = await artifact_repository.attach_bi(
        artifact["artifact_id"],
        owner_name=owner,
        bi_kind="chart",
        bi_object_id=chart_id,
        bi_dataset_id=dataset_id,
    )
    outcome.status = "converted" if converted else "failed"
    outcome.bi_view_path = converted["bi_view_path"] if converted else None
    if converted is None:
        outcome.error = "The artifact changed while it was being converted"
    return outcome


async def _convert_dashboard(
    bi: SupersetClient,
    owner: str,
    artifact_id: str,
    title: str,
    spec: dict[str, Any],
    charts: dict[str, dict[str, Any]],
    *,
    exists: bool,
    force: bool,
) -> Outcome:
    placed, texts, blockers, notes = dashboard_conversion(
        spec.get("zones", []), charts, spec.get("actions")
    )
    outcome = Outcome(artifact_id, title, "dashboard", "kept", blockers, notes)
    if blockers and not force:
        return outcome
    try:
        dashboard_id = await create_dashboard_object(
            bi, title=title, chart_artifacts=[charts[item] for item in placed], texts=texts
        )
    except BiError as exc:
        if exc.status in (401, 403):
            raise
        return Outcome(artifact_id, title, "dashboard", "failed", blockers, notes, exc.message)
    if exists:
        converted = await artifact_repository.attach_bi(
            artifact_id,
            owner_name=owner,
            bi_kind="dashboard",
            bi_object_id=dashboard_id,
            bi_dataset_id=None,
        )
    else:
        converted = await artifact_repository.create_bi(
            owner_name=owner,
            title=title,
            artifact_type="dashboard",
            sql_text="",
            database_name=None,
            schema_name=None,
            bi_kind="dashboard",
            bi_object_id=dashboard_id,
            bi_dataset_id=None,
            artifact_id=artifact_id,
        )
    outcome.status = "converted" if converted else "failed"
    outcome.bi_view_path = converted["bi_view_path"] if converted else None
    return outcome


async def legacy_report(artifact: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(blockers, notes)`` a legacy artifact would convert with, for its view.

    A dashboard converts with the legacy charts it places, so it reports
    their blockers too.
    """
    if not artifact.get("legacy"):
        return [], []
    if artifact["artifact_type"] == "chart":
        _plan, chart_blockers, chart_notes = chart_conversion(artifact)
        return chart_blockers, chart_notes
    spec = artifact.get("viz_spec") or {}
    owned = {
        row["artifact_id"]: row
        for row in await artifact_repository.list(owner_name=artifact["owner_name"])
        if row["artifact_type"] == "chart"
    }
    blockers: list[str] = []
    for chart_id in _placed_chart_ids(spec):
        chart = owned.get(chart_id)
        if chart is None:
            blockers.append(f"a chart that no longer exists ({chart_id})")
        elif chart["legacy"]:
            blockers.extend(f"{chart['title']}: {item}" for item in chart_conversion(chart)[1])
    # Its legacy charts convert first, so only the layout's own notes remain.
    converted = {
        key: {**row, "bi_object_id": row["bi_object_id"] or -1} for key, row in owned.items()
    }
    _placed, _texts, _blockers, notes = dashboard_conversion(
        spec.get("zones", []), converted, spec.get("actions")
    )
    return blockers, notes


def _placed_chart_ids(spec: dict[str, Any]) -> list[str]:
    return [
        zone["artifact_id"]
        for zone in spec.get("zones", [])
        if zone.get("kind") == "sheet" and zone.get("artifact_id")
    ]
