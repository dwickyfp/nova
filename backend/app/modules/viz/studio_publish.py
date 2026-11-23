"""Add a verified Studio result to a native Dataviz dashboard.

One server-side path serves the "Add to Dataviz" button on an agent answer or
a notebook table (docs/arch-19-studio-dataviz.md):

* :func:`check` reads each located result back from Nova's own record of it
  (:mod:`app.modules.viz.studio_results`) and decides, without running a query,
  whether a native chart can show the same thing. Supported results get a
  signed ``preview_ref`` bound to the caller; it carries no SQL or rows.
* :func:`editable_dashboards` lists the native dashboards the caller may edit
  across their personal and shared workspaces.
* :func:`publish` runs as the caller through the native dataset and document
  services: it inspects the result's SQL as a dataset, attaches it to the
  dashboard (creating the dashboard in the personal workspace when asked),
  creates a chart for the suggested visual and places it in the first free
  slot under revision CAS. Every step has an operation id derived from the
  caller and the idempotency key, so a retried request finds its receipts
  instead of applying anything twice.

Refusals are plain language for the person adding the chart.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from app.core.exceptions import NovaException

from .studio_results import (
    Locator,
    PreviewError,
    ResolvedPreview,
    normal_sql,
    resolve,
    sign,
    verify,
)

logger = logging.getLogger(__name__)

NAMESPACE = uuid5(NAMESPACE_URL, "nova:dataviz:studio-publish")
MAX_BATCH = 50
PAGE_SIZE = 20
MAX_WORKSPACES = 20
MAX_TABLE_COLUMNS = 32
MAX_MEASURES = 4
Status = Literal["SUPPORTED", "UNSUPPORTED", "UNKNOWN"]

_UI_KINDS = {"bar": "bar", "line": "line", "area": "area", "pie": "pie", "donut": "pie",
             "scatter": "scatter"}
_VEGA_KINDS = {"bar": "bar", "line": "line", "area": "area", "arc": "pie", "pie": "pie",
               "point": "scatter", "circle": "scatter", "square": "scatter", "tick": "scatter"}
_SHEET_KINDS = {"bar": "bar", "line": "line", "area": "area", "pie": "pie", "arc": "pie",
                "text": "table"}
_POINT_MARKS = frozenset({"circle", "point", "square"})
_AGGREGATIONS = {"sum": "sum", "avg": "avg", "min": "min", "max": "max", "count": "count",
                 "distinct_count": "count_distinct", "count_distinct": "count_distinct"}
_GRAINS = frozenset({"day", "week", "month", "quarter", "year"})


class PublishError(Exception):
    """A plain-language refusal with the HTTP status the route returns."""

    def __init__(self, message: str, *, status: int = 422, code: str = "NOT_PUBLISHED") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass
class Capability:
    status: Status
    reason_code: str
    message: str
    #: The suggested native chart, in result column names: ``visual`` plus the
    #: chart-shape arguments of the native Dataviz tools.
    chart: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


def _supported(chart: dict[str, Any]) -> Capability:
    return Capability("SUPPORTED", "OK", "Ready for Dataviz.", chart=chart)


def _unsupported(code: str, message: str) -> Capability:
    return Capability("UNSUPPORTED", code, message)


def _unknown(code: str, message: str) -> Capability:
    return Capability("UNKNOWN", code, message)


def _unique(rows: list[dict[str, Any]], key: str) -> bool:
    seen: set[str] = set()
    for row in rows:
        value = json.dumps(row.get(key), default=str)
        if value in seen:
            return False
        seen.add(value)
    return True


def _measures(names: list[str]) -> list[dict[str, Any]]:
    return [{"field": name, "aggregation": "sum"} for name in names]


def _ui_chart(preview: ResolvedPreview) -> Capability:
    vis = preview.visualization
    visual = _UI_KINDS.get(str(vis.get("type") or "bar"))
    x, y = vis.get("x"), vis.get("y")
    series = [item for item in vis.get("series") or [] if isinstance(item, str)]
    if visual is None or not isinstance(x, str) or not isinstance(y, str):
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            "This chart type has no Dataviz equivalent yet.")
    measures = list(dict.fromkeys(series)) if series else [y]
    missing = [name for name in [x, *measures] if name not in preview.columns]
    if missing:
        return _unknown("FIELDS_NOT_IN_RESULT",
                        "The chart names fields its result does not have: " + ", ".join(missing))
    sort: dict[str, Any] | None = None
    for step in preview.transforms:
        if step["name"] != "Sort" or not step.get("exact"):
            return _unsupported(
                "DERIVED_RESULT_NOT_REPRODUCIBLE",
                "This chart draws values the answer computed from its result "
                f"({step['name']}), which a Dataviz chart would not repeat. Ask for those "
                "figures directly and that chart can be added.",
            )
        args = step.get("args") or []
        if args and args[0] in (x, *measures):
            descending = len(args) > 1 and str(args[1]).lower().startswith("desc")
            sort = {"field": str(args[0]), "direction": "desc" if descending else "asc"}
    if visual == "pie" and len(measures) > 1:
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            "A pie shows one measure; this chart draws several.")
    if len(measures) > MAX_MEASURES:
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            f"A Dataviz chart shows at most {MAX_MEASURES} measures.")
    if visual != "scatter" and preview.rows and not _unique(preview.rows, x):
        return _unsupported(
            "NON_UNIQUE_CATEGORY",
            f"The result has several rows per {x}; a Dataviz chart would add them up and "
            "show different values.",
        )
    if visual == "scatter":
        return _supported({"visual": "scatter", "x": x, "y": measures[0]})
    chart: dict[str, Any] = {"visual": visual, "dimensions": [{"field": x}],
                             "measures": _measures(measures)}
    if visual == "pie":
        chart["donut"] = vis.get("type") == "donut"
    if sort is not None:
        chart["sort"] = sort
    return _supported(chart)


def _vega_chart(preview: ResolvedPreview) -> Capability:
    spec = preview.chart_spec or {}
    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    raw = spec.get("encoding")
    encoding: dict[str, Any] = raw if isinstance(raw, dict) else {}
    used = {key for key in encoding if key not in ("x", "y", "color", "theta", "tooltip")}
    visual = _VEGA_KINDS.get(str(mark_type or ""))
    if visual is None or used:
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            "This chart's marks or encodings have no Dataviz equivalent yet.")

    def channel(name: str) -> str | None:
        value = encoding.get(name)
        found = value.get("field") if isinstance(value, dict) else None
        return found if isinstance(found, str) and found else None

    if visual == "pie":
        category, value = channel("color"), channel("theta")
        names = [category, value]
    else:
        category, value = channel("x"), channel("y")
        names = [category, value, channel("color")]
    if not category or not value:
        return _unknown("FIELDS_NOT_IN_RESULT", "The chart's fields are not recorded.")
    missing = [n for n in names if n and preview.columns and n not in preview.columns]
    if missing:
        return _unknown("FIELDS_NOT_IN_RESULT",
                        "The chart names fields its result does not have: " + ", ".join(missing))
    if visual == "scatter":
        return _supported({"visual": "scatter", "x": category, "y": value})
    chart: dict[str, Any] = {"visual": visual, "dimensions": [{"field": category}],
                             "measures": _measures([value])}
    color = channel("color")
    if visual != "pie" and color and color != category:
        chart["color"] = color
    x_channel = encoding.get("x")
    order = x_channel.get("sort") if isinstance(x_channel, dict) else None
    if order in ("-y", "y"):
        chart["sort"] = {"field": value, "direction": "desc" if order == "-y" else "asc"}
    elif order in ("ascending", "descending"):
        chart["sort"] = {"field": category,
                         "direction": "desc" if order == "descending" else "asc"}
    return _supported(chart)


def _worksheet_chart(preview: ResolvedPreview) -> Capability:
    return worksheet_plan(preview.viz_spec, preview.columns)


def worksheet_plan(viz_spec: Any, columns: list[str]) -> Capability:
    """The native chart for a viz-core worksheet over a result with ``columns``."""
    from app.modules.viz.spec import WorksheetSpec

    try:
        sheet = WorksheetSpec.model_validate(viz_spec)
    except ValidationError:
        return _unknown("CHART_SPEC_INVALID", "The chart's stored definition cannot be read.")
    if sheet.calculations or sheet.table_calcs:
        return _unsupported("COMPUTATION_NOT_REPRESENTABLE",
                            "This chart uses calculated fields or table calculations, which "
                            "Dataviz charts do not repeat yet.")
    if sheet.filters:
        return _unsupported("COMPUTATION_NOT_REPRESENTABLE",
                            "This chart filters its rows; ask for the filtered figures directly "
                            "and that chart can be added.")
    pills = [*sheet.columns, *sheet.rows]
    dimensions: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    for pill in pills:
        if pill.is_measure:
            aggregation = _AGGREGATIONS.get(str(pill.aggregation))
            if aggregation is None:
                return _unsupported("COMPUTATION_NOT_REPRESENTABLE",
                                    f"Dataviz computes the {pill.aggregation} of "
                                    f"{pill.field} differently.")
            measures.append({"field": pill.field, "aggregation": aggregation})
        else:
            dimension: dict[str, Any] = {"field": pill.field}
            if pill.date_part in _GRAINS:
                dimension["date_grain"] = pill.date_part
            dimensions.append(dimension)
    missing = sorted({item["field"] for item in [*dimensions, *measures]}
                     - set(columns)) if columns else []
    if missing:
        return _unknown("FIELDS_NOT_IN_RESULT",
                        "The chart names fields its result does not have: " + ", ".join(missing))
    if str(sheet.marks.type) in _POINT_MARKS:
        # A scatter plots one row per mark: the x and y pills as they are.
        axes = [pill.field for pill in [*sheet.columns, *sheet.rows]]
        if len(axes) != 2 or axes[0] == axes[1]:
            return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                                "A Dataviz scatter plot needs exactly one x and one y field.")
        scatter: dict[str, Any] = {"visual": "scatter", "x": axes[0], "y": axes[1]}
        color = sheet.marks.color
        if color is not None and not color.is_measure and color.field in columns:
            scatter["color"] = color.field
        return _supported(scatter)
    visual = _SHEET_KINDS.get(str(sheet.marks.type))
    if visual is None:
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            "This chart type has no Dataviz equivalent yet.")
    if len(measures) > MAX_MEASURES or len(dimensions) > MAX_MEASURES:
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            "This chart has more fields than a Dataviz chart shows.")
    if not measures or visual == "table":
        return _supported({"visual": "table", "dimensions": dimensions, "measures": measures})
    if not dimensions:
        if len(measures) == 1:
            return _supported({"visual": "kpi", "measures": measures})
        return _supported({"visual": "table", "dimensions": [], "measures": measures})
    if visual == "pie":
        return _supported({"visual": "pie", "dimensions": dimensions[:1],
                           "measures": measures[:1]})
    chart: dict[str, Any] = {"visual": visual, "dimensions": dimensions[:1], "measures": measures}
    color = sheet.marks.color
    if color is not None and not color.is_measure and color.field != dimensions[0]["field"]:
        chart["color"] = color.field
    elif len(dimensions) > 1:
        chart["color"] = dimensions[1]["field"]
    if sheet.sort:
        chart["sort"] = {"field": sheet.sort[0].field, "direction": sheet.sort[0].direction}
    return _supported(chart)


def _table(preview: ResolvedPreview) -> Capability:
    for step in preview.transforms:
        if step["name"] != "Sort":
            return _unsupported("DERIVED_RESULT_NOT_REPRODUCIBLE",
                                f"This table shows rows the answer changed ({step['name']}). "
                                "Ask for those rows directly and the table can be added.")
    names = preview.visualization.get("columns") or preview.columns
    columns = [name for name in names if isinstance(name, str)]
    if not columns:
        return _unknown("FIELDS_NOT_IN_RESULT", "The table's columns are unknown.")
    if len(columns) > MAX_TABLE_COLUMNS:
        return _unsupported("CHART_KIND_NOT_REPRESENTABLE",
                            f"A Dataviz table shows at most {MAX_TABLE_COLUMNS} columns.")
    return _supported({"visual": "table", "dimensions": [{"field": c} for c in columns],
                       "measures": []})


def _kpi(preview: ResolvedPreview) -> Capability:
    column = preview.visualization.get("column")
    if not isinstance(column, str) or column not in preview.columns:
        return _unknown("FIELDS_NOT_IN_RESULT", "The KPI's column is not in its result.")
    if preview.transforms or len(preview.rows) != 1:
        return _unsupported("DERIVED_RESULT_NOT_REPRODUCIBLE",
                            "This KPI reads one value out of a larger result; Dataviz would "
                            "total it.")
    return _supported({"visual": "kpi", "measures": _measures([column])})


def capability(preview: ResolvedPreview) -> Capability:
    """Whether a native chart can show the same thing as this verified result."""
    if preview.render_kind in ("map", "route_map"):
        return _unsupported(
            "MAP_NOT_SUPPORTED",
            "Dataviz cannot place this map. Ask for the underlying figures (for example each "
            "location with its values) and that table can be added.",
        )
    if not preview.sql:
        return _unknown("NO_PROVENANCE",
                        preview.provenance_issue or "The query behind this result was not "
                        "recorded, so Dataviz cannot keep it up to date.")
    if preview.visualization.get("source") == "native":
        plan = preview.visualization.get("plan")
        return _supported(dict(plan)) if isinstance(plan, dict) else _unknown(
            "CHART_SPEC_INVALID", "The chart's stored definition cannot be read.")
    if preview.render_kind == "chart" and preview.locator.node is not None:
        return _ui_chart(preview)
    if preview.render_kind == "chart":
        if preview.viz_spec and preview.viz_spec.get("rows") is not None:
            return _worksheet_chart(preview)
        return _vega_chart(preview)
    if preview.render_kind == "kpi":
        return _kpi(preview)
    return _table(preview)


def _concepts(preview: ResolvedPreview) -> list[dict[str, Any]]:
    return [
        {
            "qualified_name": item.get("qualified_name"),
            "label": item.get("label"),
            "definition": item.get("definition"),
            "metric": item.get("metric"),
        }
        for item in (preview.business_context or {}).get("concepts") or []
        if isinstance(item, dict)
    ]


def _not_found(message: str) -> dict[str, Any]:
    return {"preview_ref": None, "status": "UNKNOWN", "reason_code": "PREVIEW_NOT_FOUND",
            "message": message, "can_add": False}


async def check_locator(locator: Locator, user: dict[str, Any]) -> dict[str, Any]:
    """The capability of one located result, with a signed reference to it."""
    try:
        preview = await resolve(locator, user)
    except PreviewError as exc:
        return _not_found(str(exc))
    result = capability(preview)
    supported = result.status == "SUPPORTED"
    return {
        "preview_ref": sign(locator, user) if supported else None,
        "status": result.status,
        "reason_code": result.reason_code,
        "message": result.message,
        "can_add": supported,
        "title": preview.title,
        "render_kind": preview.render_kind,
        "concepts": _concepts(preview),
    }


async def check(locators: list[Locator], user: dict[str, Any]) -> list[dict[str, Any]]:
    return [await check_locator(item, user) for item in locators[:MAX_BATCH]]


async def check_ref(ref: str, user: dict[str, Any]) -> dict[str, Any]:
    try:
        locator = verify(ref, user)
    except PreviewError as exc:
        return _not_found(str(exc))
    return await check_locator(locator, user)


# ── Dashboards the caller may edit ───────────────────────────────────


def _documents() -> Any:
    from app.modules.viz.document_service import native_document_service

    return native_document_service


def _datasets() -> Any:
    from app.modules.viz.dataset_service import native_dataset_service

    return native_dataset_service


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


async def editable_dashboards(
    user: dict[str, Any], *, search: str = "", page: int = 0,
) -> dict[str, Any]:
    """Native dashboards whose draft the caller may edit, newest first."""
    from app.modules.workspaces.spaces import space_service

    needle = search.strip().casefold()
    found: dict[str, dict[str, Any]] = {}
    for space in (await space_service.visible(user))[:MAX_WORKSPACES]:
        if not space.can_edit:
            continue
        try:
            # The document store pages at most 100 rows.
            rows = await _documents().browse(user, space.id, kind="dashboard", draft=True,
                                             limit=100)
        except NovaException:
            continue
        for row in rows:
            title = str(row.get("title") or "Untitled dashboard")
            if needle and needle not in title.casefold():
                continue
            key = str(row["resource_id"])
            found.setdefault(key, {
                "id": key,
                "title": title,
                "published": row.get("current_publication_id") is not None,
                "own": row.get("owner_name") == user["username"],
                "changed": _iso(row.get("updated_at")),
                "workspace_id": space.id,
            })
    ordered = sorted(found.values(), key=lambda item: item["changed"] or "", reverse=True)
    start = page * PAGE_SIZE
    return {"dashboards": ordered[start:start + PAGE_SIZE], "page": page,
            "has_more": len(ordered) > start + PAGE_SIZE}


# ── Publishing ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    mode: Literal["existing", "new"]
    dashboard_id: UUID | None = None
    title: str | None = None


def _explain(exc: NovaException) -> PublishError:
    status = exc.status_code
    if status == 401:
        return PublishError("Your sign-in has expired. Sign in again, then add the chart.",
                            status=401, code="SESSION_EXPIRED")
    if status == 403:
        return PublishError("Your active role cannot change this dashboard or read this data. "
                            "Nothing was changed.", status=403, code="NOT_PERMITTED")
    if status == 404:
        return PublishError("That dashboard was not found, or your active role cannot edit it.",
                            status=404, code="NOT_FOUND")
    if status == 409:
        return PublishError("The dashboard or its data changed while the chart was being "
                            "added. Try again.", status=409, code="CONFLICT")
    if status == 422:
        return PublishError("This result cannot be added to Dataviz as it is.",
                            status=422, code="NOT_REPRESENTABLE")
    if status == 504:
        return PublishError("The data took too long to answer. Try again in a moment.",
                            status=504, code="TIMEOUT")
    return PublishError("Dataviz is not available right now. Try again in a moment.",
                        status=503, code="UNAVAILABLE")


def operation_base(user: dict[str, Any], idempotency_key: str, ref: str, target: Target,
                   title: str | None) -> UUID:
    """Operation ids for one request: the same request finds its own receipts."""
    body = json.dumps([ref, target.mode, str(target.dashboard_id or ""), target.title or "",
                       title or ""], separators=(",", ":"))
    digest = hashlib.sha256(body.encode()).hexdigest()
    return uuid5(NAMESPACE, f"{user['username']}\n{idempotency_key}\n{digest}")


async def source_database(sql: str, user: dict[str, Any]) -> str:
    """The database the result's query reads, from its own qualified names."""
    from app.sql_frontend.analysis.lineage import read_query_sources
    from app.sql_frontend.errors import SQLFrontendError
    from app.sql_frontend.parser import parse_statement_async, run_sql_cpu

    fallback = str(user.get("database") or "")
    try:
        parsed = await parse_statement_async(sql)
        names = await run_sql_cpu(len(sql), read_query_sources, parsed, database=fallback)
    except SQLFrontendError:
        names = ()
    database = next((name.database for name in names if name.database), None)
    if not database:
        raise PublishError(
            "The query behind this result does not name the database it reads, so Dataviz "
            "cannot use it. Ask again naming the tables in full.",
            code="SOURCE_UNSUPPORTED",
        )
    return database


async def _once(user: dict[str, Any], operation_id: UUID, command: Any) -> dict[str, Any]:
    from app.modules.agents.tools.native_dataviz import _receipt, _settle

    prior = await _receipt(user, operation_id)
    if prior is not None:
        return prior
    return await _settle(user, operation_id, command)


async def _attach_sql(
    user: dict[str, Any], dashboard_id: UUID, base: UUID, *, dataset_id: UUID, sql: str,
    database: str, title: str,
) -> None:
    from app.modules.agents.tools.native_dataviz import _draft, _receipt, _settle
    from app.modules.viz.contracts.authoring import (
        DashboardDatasetCreate,
        SqlDatasetUpdate,
        SqlInspect,
        SqlInspection,
        SqlSelection,
    )

    attempts = [uuid5(base, f"dataset:{attempt}") for attempt in (1, 2)]
    for operation_id in attempts:
        if await _receipt(user, operation_id) is not None:
            return
    selection = SqlSelection(database=database, sql=sql)
    inspected = await _datasets().inspect(user, SqlInspect(dataset_id=dataset_id,
                                                           source=selection))
    if not isinstance(inspected, SqlInspection):
        raise PublishError("This result cannot be used as Dataviz data.",
                           code="SOURCE_UNSUPPORTED")
    request = DashboardDatasetCreate(dataset=SqlDatasetUpdate(
        dataset_id=dataset_id, source=selection, title=title[:256],
        expected_schema_fingerprint=inspected.schema_fingerprint,
    ))
    for attempt, operation_id in enumerate(attempts, start=1):
        revision, _ = await _draft(user, dashboard_id)
        try:
            await _settle(user, operation_id, partial(
                _datasets().create_in_dashboard, user, dashboard_id, request,
                operation_id=operation_id, expected_revision=revision))
            return
        except NovaException as exc:
            if exc.status_code == 409 and attempt == 1 and "SCHEMA_STALE" not in exc.message:
                continue
            raise
    raise PublishError("The dashboard keeps changing. Try again shortly.", status=409,
                       code="CONFLICT")


async def _already_on(
    user: dict[str, Any], dashboard_id: UUID, sql: str, chart_title: str,
) -> tuple[UUID | None, UUID | None]:
    """(dataset, chart) already on the dashboard for this exact query, if any.

    Adding the same answer again reuses its dataset, and does not add a second
    copy of a chart with the same title over it.
    """
    from app.modules.agents.tools.native_dataviz import _dataset, _draft
    from app.modules.viz.contracts.charts import ChartDefinition
    from app.modules.viz.contracts.dashboards import ChartWidget
    from app.modules.viz.contracts.datasets import SqlSource

    _, dashboard = await _draft(user, dashboard_id)
    dataset_id: UUID | None = None
    for reference in dashboard.datasets:
        try:
            _, dataset = await _dataset(user, reference.dataset_id, reference.revision)
        except NovaException:
            continue
        source = getattr(dataset, "source", None)
        if isinstance(source, SqlSource) and normal_sql(source.sql) == sql:
            dataset_id = reference.dataset_id
            break
    if dataset_id is None:
        return None, None
    for page in dashboard.pages:
        for widget in page.widgets:
            if not isinstance(widget, ChartWidget):
                continue
            try:
                snapshot = await _documents().read(user, widget.chart_ref.chart_id,
                                                   draft=True)
            except NovaException:
                continue
            chart = snapshot.get("definition")
            if (isinstance(chart, ChartDefinition) and chart.dataset_id == dataset_id
                    and chart.title == chart_title[:256]):
                return dataset_id, chart.chart_id
    return dataset_id, None


_TITLE_LIMIT = 60


def _words(name: Any) -> str:
    """``sell_out_units`` or ``t.stock_date`` → "sell out units", "stock date"."""
    text = str(name or "").rsplit(".", 1)[-1].replace("_", " ").strip()
    return " ".join(text.split())


def short_title(asked: str | None, answer_title: str | None, chart: dict[str, Any]) -> str:
    """A short chart title: the caller's own, the answer's when it is already a
    short label, else the measure and grouping in plain words."""
    if asked and asked.strip():
        return asked.strip()[:256]
    title = " ".join(str(answer_title or "").split())
    if title and len(title) <= _TITLE_LIMIT and not title.endswith("?"):
        return title
    measures = [_words(item.get("field")) for item in chart.get("measures") or []
                if isinstance(item, dict)]
    if not measures and chart.get("y"):
        measures = [_words(chart["y"].get("field") if isinstance(chart["y"], dict)
                           else chart["y"])]
    dimensions = [_words(item.get("field")) for item in chart.get("dimensions") or []
                  if isinstance(item, dict)]
    if not dimensions and chart.get("x"):
        dimensions = [_words(chart["x"])]
    measure = " and ".join(item for item in measures[:2] if item)
    grouping = dimensions[0] if dimensions else ""
    derived = f"{measure} by {grouping}" if measure and grouping else measure
    if derived:
        return (derived[0].upper() + derived[1:])[:_TITLE_LIMIT]
    if len(title) > _TITLE_LIMIT:
        return title[:_TITLE_LIMIT - 1].rstrip() + "…"
    return title or "Chart"


def _with_top_n(chart: Any, top_n: Any) -> Any:
    """Keep only the largest ``top_n`` categories of a bar, line, area or pie."""
    from app.modules.viz.contracts.charts import TopN

    encoding = chart.encoding
    if not top_n or getattr(encoding, "kind", None) not in {"cartesian", "category"}:
        return chart
    dimension = encoding.dimensions[0] if encoding.kind == "cartesian" else encoding.category
    measure = encoding.measures[0] if encoding.kind == "cartesian" else encoding.measure
    return chart.model_copy(update={"top_n": TopN(dimension=dimension, measure=measure,
                                                  count=int(top_n))})


def build_native_chart(
    plan: dict[str, Any], *, title: str, chart_id: UUID, dataset_id: UUID, revision: int,
    dataset: Any,
) -> Any:
    """The native chart for a suggested visual, validated against the dataset."""
    from app.modules.agents.tools.native_dataviz import (
        TEMPORAL,
        ChartSpec,
        DimensionArg,
        Fields,
        Refusal,
        build_chart,
    )
    from app.modules.viz.contracts.charts import ChartDefinition, TableEncoding

    fields = Fields(dataset)

    def dimension(item: dict[str, Any]) -> dict[str, Any]:
        # A date the result shows as is: one point per day, not per month.
        grain = item.get("date_grain")
        if grain is None and fields.field(item["field"]).logical_type in TEMPORAL:
            grain = "day"
        return {"field": item["field"], "date_grain": grain}

    shaped = dict(plan)
    top_n = shaped.pop("top_n", None)
    if shaped.get("dimensions"):
        shaped["dimensions"] = [dimension(item) for item in shaped["dimensions"]]
    if plan["visual"] != "table":
        try:
            spec = ChartSpec.model_validate({**shaped, "title": title[:256]})
        except ValidationError:
            raise PublishError("This chart cannot be drawn in Dataviz as it is.",
                               code="NOT_REPRESENTABLE") from None
        chart = build_chart(spec, chart_id=chart_id, dataset_id=dataset_id, revision=revision,
                            dataset=dataset)
        return _with_top_n(chart, top_n)
    dims = tuple(fields.dimension(DimensionArg.model_validate(item))
                 for item in shaped.get("dimensions") or [])
    from app.modules.agents.tools.native_dataviz import MeasureArg

    measures = tuple(fields.measure(MeasureArg.model_validate(item))
                     for item in shaped.get("measures") or [])
    try:
        return ChartDefinition(
            chart_id=chart_id, title=title[:256], dataset_id=dataset_id,
            dataset_revision=revision, visual="table",
            encoding=TableEncoding(dimensions=dims, measures=measures),
        )
    except ValidationError:
        raise Refusal("The table is not valid for its dataset.") from None


async def publish(
    user: dict[str, Any], *, ref: str, idempotency_key: str, target: Target,
    title: str | None = None,
) -> dict[str, Any]:
    """Add one verified result to a native dashboard, as the caller."""
    from app.modules.agents.tools.native_dataviz import (
        DEFAULT_SIZE,
        Refusal,
        _dataset,
        already_applied,
        apply_changes,
        first_free,
    )
    from app.modules.viz.contracts.commands import WidgetAdd
    from app.modules.viz.contracts.dashboards import (
        ChartReference,
        ChartWidget,
        DashboardDefinition,
        Page,
    )

    try:
        locator = verify(ref, user)
        preview = await resolve(locator, user)
    except PreviewError as exc:
        raise PublishError(str(exc), status=404, code="PREVIEW_NOT_FOUND") from exc
    result = capability(preview)
    if result.status != "SUPPORTED" or result.chart is None or not preview.sql:
        raise PublishError(result.message, code=result.reason_code)
    chart_title = short_title(title, preview.title, result.chart)
    base = operation_base(user, idempotency_key, ref, target, title)
    sql = normal_sql(preview.sql)
    try:
        database = await source_database(sql, user)
        if target.mode == "new":
            dashboard_id = uuid5(base, "dashboard")
            dashboard_title = (target.title or chart_title)[:256]
            definition = DashboardDefinition(
                dashboard_id=dashboard_id, title=dashboard_title,
                pages=(Page(page_id=uuid5(base, "page:1"), title="Page 1"),),
            )
            create = uuid5(base, "create")
            await _once(user, create, partial(
                _documents().create_visual, user, "personal", definition, operation_id=create))
        else:
            if target.dashboard_id is None:
                raise PublishError("Choose a dashboard to add the chart to.", code="BAD_TARGET")
            dashboard_id = target.dashboard_id
        workspace = await _documents().workspace_of(user, dashboard_id)
        # A replay of a request already placed returns its own first outcome;
        # only a new request looks for a copy already on the dashboard.
        place_base = uuid5(base, "place")
        replay = await already_applied(user, place_base)
        reused_dataset, reused_chart = (
            await _already_on(user, dashboard_id, sql, chart_title)
            if target.mode == "existing" and not replay else (None, None)
        )
        if reused_chart is not None:
            return {
                "operation_id": str(base),
                "status": "SUCCEEDED",
                "dashboard_id": str(dashboard_id),
                "workspace_id": workspace,
                "chart_id": str(reused_chart),
                "revision": None,
                "reused_chart": True,
                "concepts": _concepts(preview),
            }
        dataset_id = reused_dataset or uuid5(base, "dataset")
        if reused_dataset is None:
            await _attach_sql(user, dashboard_id, base, dataset_id=dataset_id, sql=sql,
                              database=database, title=chart_title)
        revision, dataset = await _dataset(user, dataset_id, None)
        chart_id = uuid5(base, "chart")
        chart = build_native_chart(result.chart, title=chart_title, chart_id=chart_id,
                                   dataset_id=dataset_id, revision=revision, dataset=dataset)
        create_chart = uuid5(base, "chart-create")
        await _once(user, create_chart, partial(
            _documents().create_visual, user, workspace, chart, operation_id=create_chart))
        widget_id = uuid5(base, "widget")

        def place(current: DashboardDefinition) -> list[Any]:
            page = current.pages[0]
            width, height = DEFAULT_SIZE.get(chart.visual, (6, 4))
            layout = first_free([widget.layout for widget in page.widgets], width, height)
            return [WidgetAdd(page_id=page.page_id, widget=ChartWidget(
                widget_id=widget_id, layout=layout,
                chart_ref=ChartReference(chart_id=chart_id, revision=1),
            ))]

        receipt = await apply_changes(user, dashboard_id, place_base, place)
    except Refusal as exc:
        raise PublishError(str(exc)) from exc
    except NovaException as exc:
        raise _explain(exc) from exc
    return {
        "operation_id": str(base),
        "status": "SUCCEEDED",
        "dashboard_id": str(dashboard_id),
        "workspace_id": workspace,
        "chart_id": str(chart_id),
        "revision": int(receipt.get("new_revision") or 0) or None,
        "reused_chart": False,
        "concepts": _concepts(preview),
    }
