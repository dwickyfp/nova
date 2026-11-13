"""Whether a Studio result can become a Dataviz chart, decided by Nova alone.

One deterministic resolver (docs/arch-19-studio-dataviz.md, D2) serves the
Agent card button, the Dataviz assistant's tools and the publish endpoint, so
none of them can disagree about what is supported. No model is called and no
expensive query runs: the decision uses the verified preview (provenance, the
stored visualization, the result's columns and rows Nova already holds), the
viz registry pinned to the installed Superset release, and Superset's health.

Statuses:

* ``SUPPORTED``: exact provenance and a Superset chart that asks the same
  business question. Only this shows **Add to Dataviz**.
* ``CONVERTIBLE``: a Superset form exists but loses something the user saw
  (a map, a route, an interaction). Never published without explicit consent
  to a named alternative.
* ``UNSUPPORTED``: no Superset equivalent keeps the question; the agent
  explains and may suggest re-asking so the governed query carries it.
* ``UNKNOWN``: not enough evidence (no recorded producer, stale context,
  Superset unavailable). Never treated as supported.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.core.metadata_scope import actor_for

from .charts import (
    SUPERSET_VERSION,
    ChartPlan,
    Measure,
    RowFilter,
    plan_from_vega,
    with_sql_order,
    worksheet_conversion,
)
from .previews import ResolvedPreview

Status = Literal["SUPPORTED", "CONVERTIBLE", "UNSUPPORTED", "UNKNOWN"]
Meaning = Literal["kept", "partial", "lost"]

#: Functions on OpenUI rows that a Superset chart reproduces exactly.
_REPRODUCIBLE = frozenset({"Sort"})
_UI_CHART_KINDS = {
    "bar": "bar",
    "line": "line",
    "area": "area",
    "pie": "pie",
    "donut": "donut",
    "scatter": "scatter",
}
_VEGA_KINDS = frozenset({"bar", "line", "area", "point", "circle", "square", "tick", "arc", "pie"})


@dataclass
class Alternative:
    strategy_id: str
    target_viz_type: str | None
    preserved: list[str]
    lost: list[str]
    meaning: Meaning
    requires_consent: bool = True
    #: ``transform``: publish another Superset form now; ``requery``: ask the
    #: question again so the governed query computes what the answer computed.
    kind: Literal["transform", "requery"] = "transform"

    @property
    def business_meaning_preserved(self) -> bool:
        return self.meaning == "kept"


@dataclass
class CapabilityResult:
    status: Status
    reason_code: str
    message: str
    provider: str = "superset"
    provider_version: str = SUPERSET_VERSION
    direct_publish_allowed: bool = False
    verified_evidence: bool = False
    alternatives: list[Alternative] = field(default_factory=list)
    #: Presentation-only differences (sort order on a legend, sizes).
    notes: list[str] = field(default_factory=list)
    #: The strict Superset chart for SUPPORTED results (and for a consented
    #: transform, the alternative's plan).
    plan: ChartPlan | None = None
    plans: dict[str, ChartPlan] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        """The internal result without plans, for APIs and tools."""
        out = asdict(self)
        out.pop("plan", None)
        out.pop("plans", None)
        out["alternatives"] = [
            {**asdict(item), "business_meaning_preserved": item.business_meaning_preserved}
            for item in self.alternatives
        ]
        return out


_health: dict[str, float | bool] = {"at": 0.0, "ok": False}
_HEALTH_TTL = 20.0


async def provider_available() -> bool:
    """Superset answers its health check; cached briefly per process."""
    now = time.monotonic()
    if now - float(_health["at"]) < _HEALTH_TTL:
        return bool(_health["ok"])
    ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.SUPERSET_INTERNAL_URL.rstrip('/')}/bi/health")
            ok = response.status_code == 200
    except httpx.HTTPError:
        ok = False
    _health.update(at=now, ok=ok)
    return ok


def _unknown(code: str, message: str) -> CapabilityResult:
    return CapabilityResult(status="UNKNOWN", reason_code=code, message=message)


def _requery(lost: list[str]) -> Alternative:
    return Alternative(
        strategy_id="requery.governed",
        target_viz_type=None,
        preserved=["the question", "the governed source", "your role's data access"],
        lost=lost,
        meaning="kept",
        kind="requery",
    )


def _unique(rows: list[dict[str, Any]], keys: list[str]) -> bool:
    seen: set[tuple] = set()
    for row in rows:
        key = tuple(str(row.get(k)) for k in keys)
        if key in seen:
            return False
        seen.add(key)
    return True


def _ui_chart(preview: ResolvedPreview, rows: list[dict[str, Any]]) -> CapabilityResult:
    vis = preview.visualization
    kind = _UI_CHART_KINDS.get(str(vis.get("type") or "bar"))
    x, y = vis.get("x"), vis.get("y")
    series = [s for s in vis.get("series") or [] if isinstance(s, str)]
    if kind is None or not isinstance(x, str) or not isinstance(y, str):
        return CapabilityResult(
            status="UNSUPPORTED",
            reason_code="CHART_KIND_NOT_REPRESENTABLE",
            message="This chart type has no Dataviz equivalent.",
            alternatives=[_requery(["this chart type"])],
        )
    # The native chart draws its ``series`` columns as the measures when it
    # names any, and ``y`` otherwise (data-chart.tsx).
    measures = list(dict.fromkeys(series)) if series else [y]
    missing = [name for name in [x, *measures] if name not in preview.columns]
    if missing:
        return _unknown(
            "FIELDS_NOT_IN_RESULT",
            "The chart names fields its result does not have: " + ", ".join(missing),
        )
    order = None
    for step in preview.transforms:
        if step["name"] not in _REPRODUCIBLE or not step.get("exact"):
            return CapabilityResult(
                status="UNSUPPORTED",
                reason_code="DERIVED_RESULT_NOT_REPRODUCIBLE",
                message=(
                    "This chart draws values the answer computed from its result "
                    f"({step['name']}), which a Dataviz chart over the source would not repeat."
                ),
                verified_evidence=True,
                alternatives=[_requery([f"the answer's local {step['name']} step"])],
            )
        args = step.get("args") or []
        if len(args) >= 1 and args[0] in (x, *measures):
            descending = len(args) > 1 and str(args[1]).lower().startswith("desc")
            by_value = args[0] != x
            order = ("value_" if by_value else "x_") + ("desc" if descending else "asc")
    if kind in ("pie", "donut") and len(measures) > 1:
        return CapabilityResult(
            status="UNSUPPORTED",
            reason_code="CHART_KIND_NOT_REPRESENTABLE",
            message="A pie shows one measure; this one draws several.",
            alternatives=[_requery(["the extra measures"])],
        )
    # The native chart draws one mark per row. Superset aggregates per
    # category; that is the same chart only when each category is one row.
    if rows and kind != "scatter" and not _unique(rows, [x]):
        return CapabilityResult(
            status="UNSUPPORTED",
            reason_code="NON_UNIQUE_CATEGORY",
            message=(
                f"The result has several rows per {x}; a Dataviz chart would add them up "
                "and show different values."
            ),
            verified_evidence=True,
            alternatives=[_requery([f"one mark per row for each {x}"])],
        )
    plan = ChartPlan(
        kind=kind,  # type: ignore[arg-type]
        x=x,
        measures=[Measure(column=m) for m in measures[: 1 if kind in ("pie", "donut") else 8]],
        order=order if kind in ("bar", "line", "area", "scatter") else None,  # type: ignore[arg-type]
    )
    return CapabilityResult(
        status="SUPPORTED",
        reason_code="OK",
        message="Ready for Dataviz.",
        direct_publish_allowed=True,
        verified_evidence=True,
        plan=plan,
    )


def _block_chart(preview: ResolvedPreview) -> CapabilityResult:
    if preview.viz_spec and preview.viz_spec.get("rows") is not None:
        try:
            plan, blockers, notes = worksheet_conversion(preview.viz_spec)
        except ValueError:
            return _unknown("CHART_SPEC_INVALID", "The chart's stored definition cannot be read.")
        if blockers:
            return CapabilityResult(
                status="UNSUPPORTED",
                reason_code="COMPUTATION_NOT_REPRESENTABLE",
                message="Dataviz computes this chart differently: " + "; ".join(blockers) + ".",
                verified_evidence=True,
                alternatives=[_requery(blockers)],
            )
        missing = sorted(plan.referenced() - set(preview.columns)) if preview.columns else []
        if missing:
            return _unknown(
                "FIELDS_NOT_IN_RESULT",
                "The chart names fields its result does not have: " + ", ".join(missing),
            )
        if plan.kind == "table" and preview.visualization.get("kind") not in ("text", "table"):
            return CapabilityResult(
                status="UNSUPPORTED",
                reason_code="CHART_KIND_NOT_REPRESENTABLE",
                message="This chart has no Dataviz chart of the same kind.",
                alternatives=[_requery(["this chart type"])],
            )
        return CapabilityResult(
            status="SUPPORTED",
            reason_code="OK",
            message="Ready for Dataviz.",
            direct_publish_allowed=True,
            verified_evidence=True,
            plan=with_sql_order(plan, preview.sql or ""),
            notes=notes,
        )
    spec = preview.chart_spec or {}
    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    raw_encoding = spec.get("encoding")
    encoding: dict[str, Any] = raw_encoding if isinstance(raw_encoding, dict) else {}
    used = {key for key in encoding if key not in ("x", "y", "color", "theta", "tooltip")}
    if mark_type not in _VEGA_KINDS or used:
        return CapabilityResult(
            status="UNSUPPORTED",
            reason_code="CHART_KIND_NOT_REPRESENTABLE",
            message="This chart's marks or encodings have no Dataviz equivalent.",
            alternatives=[_requery(["this chart's encodings"])],
        )
    plan = plan_from_vega(spec, preview.columns or None)
    if plan.kind == "table":
        return _unknown("FIELDS_NOT_IN_RESULT", "The chart's fields are not in its result.")
    return CapabilityResult(
        status="SUPPORTED",
        reason_code="OK",
        message="Ready for Dataviz.",
        direct_publish_allowed=True,
        verified_evidence=True,
        plan=with_sql_order(plan, preview.sql or ""),
    )


def _table(preview: ResolvedPreview) -> CapabilityResult:
    columns = preview.visualization.get("columns") or preview.columns
    columns = [c for c in columns if isinstance(c, str)][:64]
    for step in preview.transforms:
        if step["name"] not in _REPRODUCIBLE:
            return CapabilityResult(
                status="UNSUPPORTED",
                reason_code="DERIVED_RESULT_NOT_REPRODUCIBLE",
                message=f"This table shows rows the answer changed locally ({step['name']}).",
                verified_evidence=True,
                alternatives=[_requery([f"the local {step['name']} step"])],
            )
    if not columns:
        return _unknown("FIELDS_NOT_IN_RESULT", "The table's columns are unknown.")
    return CapabilityResult(
        status="SUPPORTED",
        reason_code="OK",
        message="Ready for Dataviz.",
        direct_publish_allowed=True,
        verified_evidence=True,
        plan=ChartPlan(kind="table", columns=columns),
    )


_GRAIN_CODES = {"day": "P1D", "week": "P1W", "month": "P1M", "quarter": "P3M", "year": "P1Y"}


def _dataset_answer(preview: ResolvedPreview) -> CapabilityResult:
    """An answer read from the open dashboard's own dataset (``dataviz_query``).

    Dataviz draws it again on that dataset with the same metrics, columns and
    period. The dashboard's filters are not fixed into the chart: they apply to
    every chart on the dashboard already. A period comparison was computed by
    Nova from two queries; a Superset chart draws one period, so it is refused.
    """
    source = preview.dataset_source or {}
    if source.get("compare_period"):
        return CapabilityResult(
            status="UNSUPPORTED",
            reason_code="COMPUTED_COMPARISON",
            message=(
                "This table compares two periods and was computed by Nova from two queries; "
                "a Dataviz chart shows one period. Ask for one period (for example net revenue "
                "by region in October 2026) and that table can be added."
            ),
            verified_evidence=True,
            alternatives=[_requery(["the comparison with the earlier period"])],
        )
    measures = [
        Measure(column=str(item["saved"]), saved=True)
        if item.get("saved")
        else Measure(column=str(item["column"]), aggregate=item["aggregate"])
        for item in source.get("measures") or []
        if isinstance(item, dict)
    ]
    dimensions = [str(d) for d in source.get("dimensions") or []]
    filters = (
        [
            RowFilter(
                column=str(source["time_column"]),
                operator="TEMPORAL_RANGE",
                values=[str(source["period"])],
            )
        ]
        if source.get("period") and source.get("time_column")
        else []
    )
    return CapabilityResult(
        status="SUPPORTED",
        reason_code="OK",
        message="Ready for Dataviz: drawn on the dashboard's own dataset.",
        direct_publish_allowed=True,
        verified_evidence=True,
        notes=["The dashboard's filters keep applying to this chart."],
        plan=ChartPlan(
            kind="table",
            columns=dimensions,
            measures=measures,
            filters=filters,
            time_grain=_GRAIN_CODES.get(str(source.get("time_grain") or "")),
        ),
    )


def _kpi(preview: ResolvedPreview, rows: list[dict[str, Any]]) -> CapabilityResult:
    column = preview.visualization.get("column")
    if not isinstance(column, str) or column not in preview.columns:
        return _unknown("FIELDS_NOT_IN_RESULT", "The KPI's column is not in its result.")
    if preview.transforms or len(rows) != 1:
        return CapabilityResult(
            status="UNSUPPORTED",
            reason_code="DERIVED_RESULT_NOT_REPRODUCIBLE",
            message="This KPI reads one value out of a larger result; Dataviz would total it.",
            verified_evidence=True,
            alternatives=[_requery(["the selected row"])],
        )
    return CapabilityResult(
        status="SUPPORTED",
        reason_code="OK",
        message="Ready for Dataviz.",
        direct_publish_allowed=True,
        verified_evidence=True,
        plan=ChartPlan(kind="big_number", measures=[Measure(column=column)]),
    )


def _map(preview: ResolvedPreview) -> CapabilityResult:
    vis = preview.visualization
    located = [
        c
        for c in (vis.get("label"), vis.get("latitude"), vis.get("longitude"), vis.get("sequence"))
        if isinstance(c, str)
    ]
    measures = [c for c in preview.columns if c not in located and c != vis.get("geometry")]
    columns = list(dict.fromkeys([*located, *measures]))[:64]
    if preview.render_kind == "route_map":
        sequence = vis.get("sequence")
        table = Alternative(
            strategy_id="table.route_stops",
            target_viz_type="table",
            preserved=["each stop", "its visit order", "the stop's figures"],
            lost=[
                "the map",
                "the route lines and road geometry",
                "travel distance",
                "selecting a stop on the map",
            ],
            meaning="partial",
        )
        plans = {table.strategy_id: ChartPlan(kind="table", columns=columns)}
        return CapabilityResult(
            status="CONVERTIBLE",
            reason_code="INTERACTION_NOT_REPRESENTABLE",
            message=(
                "Dataviz has no interactive route map. A point map would drop the route, "
                "so it is not offered; a stop table in visit order keeps the stops and their "
                f"order{f' ({sequence})' if sequence else ''} but not the map."
            ),
            verified_evidence=True,
            alternatives=[table, _requery(["the map"])],
            plans=plans,
        )
    table = Alternative(
        strategy_id="table.locations",
        target_viz_type="table",
        preserved=["each location", "its figures"],
        lost=["the map", "zooming and selecting places"],
        meaning="partial",
    )
    return CapabilityResult(
        status="CONVERTIBLE",
        reason_code="INTERACTION_NOT_REPRESENTABLE",
        message=(
            "Dataviz in this deployment has no base map for map charts, so the map cannot "
            "be placed as it is. A location table keeps the places and their figures."
        ),
        verified_evidence=True,
        alternatives=[table],
        plans={table.strategy_id: ChartPlan(kind="table", columns=columns)},
    )


async def check(preview: ResolvedPreview) -> CapabilityResult:
    """The capability of one verified preview."""
    rows = preview.rows
    if preview.dataset_source is not None:
        result = _dataset_answer(preview)
    elif preview.render_kind == "route_map" or preview.render_kind == "map":
        result = _map(preview)
    elif not preview.sql:
        return _unknown(
            "NO_PROVENANCE",
            preview.provenance_issue or "The query behind this result was not recorded.",
        )
    elif preview.render_kind == "chart" and preview.locator.node is not None:
        result = _ui_chart(preview, rows)
    elif preview.render_kind == "chart":
        result = _block_chart(preview)
    elif preview.render_kind == "kpi":
        result = _kpi(preview, rows)
    else:
        result = _table(preview)
    if (
        result.status in ("SUPPORTED", "CONVERTIBLE")
        and not preview.sql
        and preview.dataset_source is None
    ):
        # A map whose rows were computed (route optimisation, clustering) has
        # no governed query Dataviz could run again: explain, never convert.
        computed = preview.producer_tool not in (None, "semantic_query", "query_execute")
        result.status = "UNSUPPORTED" if computed else "UNKNOWN"
        result.reason_code = "COMPUTED_RESULT_NOT_QUERYABLE" if computed else "NO_PROVENANCE"
        result.message = (
            (
                f"This view was computed by {preview.producer_tool} in the conversation, not "
                "read by a governed query, so Dataviz cannot keep it up to date. "
                if computed
                else "The query behind this view was not recorded. "
            )
            + "A point map would lose the route and is not offered. Ask for the underlying "
            "figures (for example each stop or route with its distance) as a governed query, "
            "and that table can be added."
        )
        result.direct_publish_allowed = False
        result.plans = {}
        result.alternatives = [
            Alternative(
                strategy_id="requery.governed",
                target_viz_type="table",
                preserved=["the stops or routes and their figures, from governed data"],
                lost=["the map", "the computed route order and road geometry"],
                meaning="partial",
                kind="requery",
            )
        ]
    if result.status in ("SUPPORTED", "CONVERTIBLE") and not await provider_available():
        return _unknown("PROVIDER_UNAVAILABLE", "Dataviz is unavailable right now.")
    return result


async def may_publish(user: dict[str, Any]) -> bool:
    """Whether the active role is a BI author (checked apart from capability)."""
    from .access import SUPERSET_VIEWER, superset_role_for

    return (
        await superset_role_for(str(user.get("active_role") or ""), actor=actor_for(user))
        != SUPERSET_VIEWER
    )
