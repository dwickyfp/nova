"""Studio tools for native Dataviz dashboards (docs/arch-19-studio-dataviz.md).

They run inside the shared bounded ``AssistantLoop`` and call the native
document and dataset services in-process as the caller: the session's
principal and active role, revalidated by every service command. There is no
service credential and no second authorization path.

* Reads (list, read, describe, query) are read-only and need no prompt.
* Every change (add charts, update a chart, arrange widgets, filters, create,
  attach a dataset, publish) is a ``session_change`` the user approves, like
  the other Dataviz writes. Each commits one revision through revision CAS and
  returns that receipt's revision; a conflict is re-read and retried once.
* Operation ids derive from the tool call, so a retried call finds its own
  receipt instead of applying the change twice. An outcome the service could
  not confirm is resolved by its receipt, never by sending it again.

Results, errors and consent previews are written for the person using the
dashboard: no internal names, addresses or status codes.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Annotated, Any, Literal, TypeVar, cast, get_args
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.exceptions import NovaException
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.viz.contracts.charts import (
    CartesianEncoding,
    CategoryEncoding,
    ChartDefinition,
    ChartSort,
    ChartStyle,
    ConditionalFormat,
    Dimension,
    Measure,
    ScatterEncoding,
    SingleMeasureEncoding,
    TableEncoding,
)
from app.modules.viz.contracts.commands import (
    DatasetsReplace,
    FiltersReplace,
    LayoutChange,
    Operation,
    Placement,
    PublicationRequest,
    WidgetAdd,
    WidgetRemove,
    WidgetReplace,
)
from app.modules.viz.contracts.common import (
    BooleanValue,
    DateValue,
    DecimalValue,
    FloatValue,
    IntegerValue,
    StringValue,
    TimestampValue,
)
from app.modules.viz.contracts.dashboards import (
    ChartReference,
    ChartWidget,
    DashboardDefinition,
    DatasetReference,
    Layout,
    Page,
)
from app.modules.viz.contracts.datasets import DatasetDefinition, DatasetField
from app.modules.viz.contracts.filters import (
    And,
    FieldBinding,
    FilterDefinition,
    FilterScope,
    FilterTree,
    Predicate,
)
from app.modules.viz.contracts.http import DatasetPublication

logger = logging.getLogger(__name__)

NAMESPACE = uuid5(NAMESPACE_URL, "nova:dataviz:native-assistant")
#: Bounds on what one result carries back to the model.
MAX_DASHBOARDS = 50
MAX_WORKSPACES = 20
MAX_CHART_READS = 24
MAX_FIELDS = 80
MAX_QUERY_ROWS = 200
#: Bounds on an approval text: widgets listed by name, and charts read to name them.
MAX_LISTED = 10
MAX_PREVIEW_CHART_READS = 60
NUMERIC = frozenset({"integer", "decimal", "float"})
TEMPORAL = frozenset({"date", "timestamp"})
VISUAL_WORDS = {
    "bar": "bar chart", "line": "line chart", "area": "area chart", "pie": "pie chart",
    "kpi": "KPI", "table": "table", "pivot": "pivot table", "scatter": "scatter plot",
}
DEFAULT_SIZE = {"kpi": (3, 2)}


class Refusal(Exception):
    """A plain-language reason a request cannot be done as asked."""


# ── Services (in-process, as the caller) ─────────────────────────────


def _documents() -> Any:
    from app.modules.viz.document_service import native_document_service

    return native_document_service


def _datasets() -> Any:
    from app.modules.viz.dataset_service import native_dataset_service

    return native_dataset_service


async def _spaces(user: dict[str, Any]) -> list[Any]:
    from app.modules.workspaces.spaces import space_service

    return await space_service.visible(user)


# ── Arguments ────────────────────────────────────────────────────────

FieldName = Annotated[str, Field(min_length=1, max_length=256)]
Grain = Literal["day", "week", "month", "quarter", "year"]
Aggregation = Literal["sum", "avg", "min", "max", "count", "count_distinct"]
VisualName = Literal["bar", "line", "area", "pie", "kpi", "table", "pivot", "scatter"]
FilterValue = Annotated[str, Field(max_length=4096)] | int | float | bool
Title = Annotated[str, Field(min_length=1, max_length=256)]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


ArgsT = TypeVar("ArgsT", bound=_Args)


class DimensionArg(_Args):
    field: FieldName
    date_grain: Grain | None = None

    @model_validator(mode="before")
    @classmethod
    def plain_name(cls, value: Any) -> Any:
        return {"field": value} if isinstance(value, str) else value


class MeasureArg(_Args):
    field: FieldName | None = None
    aggregation: Aggregation = "sum"

    @model_validator(mode="after")
    def counted(self) -> MeasureArg:
        if self.field is None and self.aggregation != "count":
            raise ValueError("only a row count may leave out its field")
        return self


STACKS: tuple[str, ...] = get_args(CartesianEncoding.model_fields["stack"].annotation)


class SortArg(_Args):
    field: FieldName
    direction: Literal["asc", "desc"] = "desc"


class ChartShape(_Args):
    """What a chart shows, in dataset field names."""

    dimensions: list[DimensionArg] | None = Field(default=None, max_length=4)
    measures: list[MeasureArg] | None = Field(default=None, max_length=4)
    x: FieldName | None = None
    y: FieldName | None = None
    size: FieldName | None = None
    color: FieldName | None = None
    orientation: Literal["vertical", "horizontal"] | None = None
    #: The contract's stacking modes (``CartesianEncoding.stack``).
    stack: str | None = Field(default=None, max_length=16)
    donut: bool | None = None
    sort: SortArg | None = None
    #: Pivot: the fields across the top.
    columns: list[DimensionArg] | None = Field(default=None, max_length=3)
    #: Table or pivot: a total row (and column, for a pivot).
    totals: bool | None = None
    #: Table or pivot: measures to shade with a colour scale.
    shade: list[FieldName] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def known_stack(self) -> ChartShape:
        if self.stack is not None and self.stack not in STACKS:
            raise ValueError(f"stack must be one of {', '.join(STACKS)}")
        return self


class ChartSpec(ChartShape):
    title: Title
    visual: VisualName
    dataset_id: UUID | None = None
    width: int | None = Field(default=None, ge=2, le=12)
    height: int | None = Field(default=None, ge=2, le=48)


class ListArgs(_Args):
    search: str | None = Field(default=None, max_length=120)


class DashboardArgs(_Args):
    dashboard_id: UUID | None = None


class DescribeArgs(_Args):
    dataset_id: UUID
    revision: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)


class QueryFilterArg(_Args):
    field: FieldName
    op: Literal[
        "eq", "neq", "in", "not_in", "lt", "lte", "gt", "gte", "between", "is_null",
        "is_not_null",
    ] = "eq"
    values: list[FilterValue] = Field(default_factory=list, max_length=100)


class QueryArgs(_Args):
    dataset_id: UUID
    revision: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
    dimensions: list[DimensionArg] = Field(default_factory=list, max_length=4)
    measures: list[MeasureArg] = Field(default_factory=list, max_length=6)
    filters: list[QueryFilterArg] = Field(default_factory=list, max_length=8)
    sort: SortArg | None = None
    limit: int = Field(default=50, ge=1, le=MAX_QUERY_ROWS)

    @model_validator(mode="after")
    def something(self) -> QueryArgs:
        if not self.dimensions and not self.measures:
            raise ValueError("ask for at least one dimension or measure")
        return self


class AddChartsArgs(_Args):
    dashboard_id: UUID | None = None
    page_id: UUID | None = None
    charts: list[ChartSpec] = Field(min_length=1, max_length=6)


class UpdateChartArgs(ChartShape):
    dashboard_id: UUID | None = None
    widget_id: UUID
    title: Title | None = None
    visual: VisualName | None = None


class MoveArg(_Args):
    widget_id: UUID
    x: int | None = Field(default=None, ge=0, le=11)
    y: int | None = Field(default=None, ge=0, le=10_000)
    w: int | None = Field(default=None, ge=2, le=12)
    h: int | None = Field(default=None, ge=2, le=48)


class ArrangeArgs(_Args):
    dashboard_id: UUID | None = None
    move: list[MoveArg] = Field(default_factory=list, max_length=50)
    remove: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def something(self) -> ArrangeArgs:
        if not self.move and not self.remove:
            raise ValueError("name widgets to move, resize or remove")
        ids = [item.widget_id for item in self.move]
        if len(set(ids)) != len(ids) or len(set(self.remove)) != len(self.remove):
            raise ValueError("name each widget once")
        if set(ids) & set(self.remove):
            raise ValueError("a widget cannot be moved and removed together")
        return self


class FilterArg(_Args):
    title: Title
    field: FieldName
    dataset_id: UUID | None = None
    kind: Literal[
        "multi_value", "single_value", "date_range", "number_range_slider", "text_entry",
        "boolean_switch",
    ] = "multi_value"
    defaults: list[FilterValue] = Field(default_factory=list, max_length=50)
    scope: Literal["global", "page"] = "global"
    page_id: UUID | None = None


class FiltersArgs(_Args):
    dashboard_id: UUID | None = None
    mode: Literal["add", "replace"] = "add"
    filters: list[FilterArg] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def something(self) -> FiltersArgs:
        if self.mode == "add" and not self.filters:
            raise ValueError("name at least one filter to add")
        return self


class TableSourceArg(_Args):
    database: Annotated[str, Field(min_length=1, max_length=128)]
    table: Annotated[str, Field(min_length=1, max_length=128)]
    kind: Literal["table", "view"] = "table"
    columns: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(
        default=None, min_length=1, max_length=200,
    )


class DatasetChoice(_Args):
    dataset_id: UUID | None = None
    table: TableSourceArg | None = None
    dataset_title: Title | None = None

    @model_validator(mode="after")
    def one_source(self) -> DatasetChoice:
        if self.dataset_id is not None and self.table is not None:
            raise ValueError("choose an existing dataset or a table, not both")
        return self


class CreateArgs(DatasetChoice):
    title: Title
    workspace_id: str = Field(default="personal", min_length=1, max_length=128,
                              pattern=r"^(personal|[0-9a-f]{32})$")


class AttachArgs(DatasetChoice):
    dashboard_id: UUID | None = None

    @model_validator(mode="after")
    def needs_source(self) -> AttachArgs:
        if self.dataset_id is None and self.table is None:
            raise ValueError("name a dataset or a table to attach")
        return self


class PublishArgs(_Args):
    dashboard_id: UUID | None = None
    release_notes: str = Field(default="", max_length=2000)


def _parse(model: type[ArgsT], arguments: dict[str, Any]) -> ArgsT:
    """Strict validation; the reason never echoes the supplied values."""
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        reasons = []
        for error in exc.errors(include_input=False, include_url=False)[:4]:
            where = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
            message = str(error.get("msg") or "is invalid").removeprefix("Value error, ")
            reasons.append(f"{where}: {message}" if where else message)
        raise Refusal("The request is incomplete or invalid: " + "; ".join(reasons)) from None


# ── Errors ───────────────────────────────────────────────────────────

_UNCERTAIN = frozenset({500, 502, 503, 504})


def _explain(exc: NovaException) -> str:
    status = exc.status_code
    if status == 401:
        return "Your sign-in has expired. Sign in again, then ask once more."
    if status == 403:
        return (
            "Your active role cannot open or change this. Switch to a role that can edit it, "
            "or ask its owner for access."
        )
    if status == 404:
        return "That dashboard, chart or dataset was not found, or your active role cannot see it."
    if status == 409:
        if "SCHEMA_STALE" in exc.message:
            return "The dataset's source changed since it was saved; it has to be updated first."
        return "The dashboard changed while this was being saved. Read it again, then retry."
    if status == 422:
        return "The dashboard cannot accept this change as described."
    if status == 504:
        return "The data took too long to answer. Try a narrower question."
    return "The dashboard service is not available right now. Try again in a moment."


def _query_refusal(exc: NovaException) -> str:
    if exc.status_code == 403:
        return "The dataset's data is not available to your active role."
    if exc.status_code == 422:
        return (
            "This question cannot be answered from the dataset as asked. Try other fields, "
            "a simpler grouping or fewer filters."
        )
    return _explain(exc)


def _fail(message: str, *, recoverable: bool = False) -> ToolOutcome:
    """A refusal Neva can correct (a wrong field, a missing argument) stays in the turn."""
    return ToolOutcome(
        ok=False, summary="", error=message, safe_detail=message,
        error_class="INVALID_REQUEST" if recoverable else None, recoverable=recoverable,
    )


def _user(context: Any) -> dict[str, Any] | None:
    user = getattr(context, "user", None)
    return user if isinstance(user, dict) else None


def _open(context: Any) -> dict[str, Any]:
    dataviz = getattr(context, "dataviz", None) or {}
    native = dataviz.get("native_dashboard") if isinstance(dataviz, dict) else None
    return native if isinstance(native, dict) else {}


def _uuid(value: Any) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else UUID(str(value)) if value else None
    except ValueError:
        return None


def _dashboard_id(requested: UUID | None, context: Any) -> UUID:
    identity = requested or _uuid(_open(context).get("dashboard_id"))
    if identity is None:
        raise Refusal("No dashboard is open. Name the dashboard to use, or create one.")
    return identity


def _base(invocation: ToolInvocation) -> UUID:
    """Operation ids for one tool call: a retried call finds its own receipts."""
    return uuid5(NAMESPACE, invocation.tool_call_id) if invocation.tool_call_id else uuid4()


# ── Reading ──────────────────────────────────────────────────────────


async def _draft(user: dict[str, Any], dashboard_id: UUID) -> tuple[int, DashboardDefinition]:
    snapshot = await _documents().read(user, dashboard_id, draft=True)
    definition = snapshot["definition"]
    if not isinstance(definition, DashboardDefinition):
        raise Refusal("That is not a dashboard.")
    return int(snapshot["revision"]), definition


async def _readable(
    user: dict[str, Any], dashboard_id: UUID,
) -> tuple[int, DashboardDefinition, bool, bool]:
    """The draft for an editor, else the published version: (revision, definition,
    editable, published)."""
    try:
        snapshot = await _documents().read(user, dashboard_id, draft=True)
        editable = True
    except NovaException as exc:
        if exc.status_code not in (403, 404):
            raise
        snapshot = await _documents().read(user, dashboard_id)
        editable = False
    definition = snapshot["definition"]
    if not isinstance(definition, DashboardDefinition):
        raise Refusal("That is not a dashboard.")
    return (int(snapshot["revision"]), definition, editable,
            snapshot.get("publication_id") is not None)


Dataset = DatasetDefinition | DatasetPublication


async def _dataset(
    user: dict[str, Any], dataset_id: UUID, revision: int | None,
) -> tuple[int, Dataset]:
    """A dataset at ``revision`` (or its latest the caller may read)."""
    async def load(pinned: int | None) -> dict[str, Any]:
        try:
            return await _documents().read(user, dataset_id, draft=True, revision=pinned)
        except NovaException as exc:
            if exc.status_code not in (403, 404, 422):
                raise
            # A viewer reads the exact version a publication pins (or the latest).
            return await _documents().read(user, dataset_id, revision=pinned)

    try:
        snapshot = await load(revision)
    except NovaException as exc:
        if revision is None or exc.status_code not in (404, 422):
            raise
        # An unknown revision number is not a reason to fail; use the readable version.
        snapshot = await load(None)
    definition = snapshot["definition"]
    if not isinstance(definition, (DatasetDefinition, DatasetPublication)):
        raise Refusal("That is not a dataset.")
    return int(snapshot["revision"]), definition


class Fields:
    """Dataset fields and metrics, found by id or by name (any case)."""

    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset
        self.fields = {item.field_id: item for item in dataset.fields}
        self.metrics = {str(item.metric_id): item for item in dataset.metrics}
        self._names: dict[str, str] = {}
        for identity, item in [*self.fields.items(), *self.metrics.items()]:
            self._names.setdefault(item.name.casefold(), identity)
            self._names.setdefault(identity.casefold(), identity)

    def resolve(self, name: str) -> str:
        identity = self._names.get(name.strip().casefold())
        if identity is None:
            known = ", ".join(item.name for item in list(self.fields.values())[:30])
            raise Refusal(f"The dataset has no field named '{name}'. Its fields: {known}.")
        return identity

    def field(self, name: str) -> DatasetField:
        identity = self.resolve(name)
        if identity not in self.fields:
            raise Refusal(f"'{name}' is a saved metric; use it as a measure.")
        return self.fields[identity]

    def dimension(self, arg: DimensionArg) -> Dimension:
        field = self.field(arg.field)
        if field.logical_type == "geometry":
            raise Refusal(f"'{field.name}' is a location shape and cannot group a chart.")
        if field.logical_type not in TEMPORAL:
            # A date level means nothing on text or numbers; models often send one
            # anyway, so it is ignored rather than ending the question.
            return Dimension(field_id=field.field_id, date_grain=None)
        grain = arg.date_grain or "month"
        return Dimension(field_id=field.field_id, date_grain=grain)

    def measure(self, arg: MeasureArg, series: Literal["bar", "line", "area"] = "bar") -> Measure:
        if arg.field is None:
            counted = next((item for item in self.fields.values() if not item.nullable), None)
            if counted is None:
                raise Refusal("Name the field to count.")
            return Measure(field_id=counted.field_id, aggregation="count", series=series)
        identity = self.resolve(arg.field)
        metric = self.metrics.get(identity)
        if metric is not None:
            aggregation = "agg" if metric.level != "row" else arg.aggregation
            return Measure(field_id=identity, aggregation=aggregation, series=series)
        field = self.fields[identity]
        if arg.aggregation not in {"count", "count_distinct"} and field.logical_type not in NUMERIC:
            raise Refusal(
                f"'{field.name}' is not a number, so it can only be counted "
                "(count or count_distinct)."
            )
        return Measure(field_id=identity, aggregation=arg.aggregation, series=series)

    def numeric(self, name: str) -> str:
        field = self.field(name)
        if field.logical_type not in NUMERIC:
            raise Refusal(f"'{field.name}' is not a number.")
        return field.field_id

    def name(self, identity: str) -> str:
        item = self.fields.get(identity) or self.metrics.get(identity)
        return item.name if item is not None else identity


def _field_summary(dataset: Dataset) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in dataset.fields[:MAX_FIELDS]:
        entry: dict[str, Any] = {
            "name": item.name, "type": item.logical_type, "role": item.semantic_role,
        }
        if item.unit:
            entry["unit"] = item.unit
        if item.currency:
            entry["currency"] = item.currency
        out.append(entry)
    for metric in dataset.metrics[:32]:
        out.append({"name": metric.name, "type": "metric", "role": "measure",
                    "unit": metric.unit})
    return out


def _chart_summary(chart: ChartDefinition, fields: Fields | None) -> dict[str, Any]:
    def name(identity: str) -> str:
        return fields.name(identity) if fields else identity

    encoding = chart.encoding
    summary: dict[str, Any] = {"chart_title": chart.title, "visual": chart.visual}
    dimensions: list[str] = []
    measures: list[str] = []
    if isinstance(encoding, (CartesianEncoding, TableEncoding)):
        dimensions = [name(item.field_id) for item in encoding.dimensions]
        measures = [f"{item.aggregation}({name(item.field_id)})" for item in encoding.measures]
    elif isinstance(encoding, CategoryEncoding):
        dimensions = [name(encoding.category.field_id)]
        measures = [f"{encoding.measure.aggregation}({name(encoding.measure.field_id)})"]
    elif isinstance(encoding, SingleMeasureEncoding):
        measures = [f"{encoding.measure.aggregation}({name(encoding.measure.field_id)})"]
    elif isinstance(encoding, ScatterEncoding):
        measures = [name(encoding.x), name(encoding.y)]
    if dimensions:
        summary["dimensions"] = dimensions
    if measures:
        summary["measures"] = measures
    return summary


async def dashboard_summary(
    user: dict[str, Any], dashboard_id: UUID, *, selected: UUID | None = None,
) -> dict[str, Any]:
    """Pages, widgets, datasets (with fields) and filters, read as the caller."""
    revision, dashboard, editable, published = await _readable(user, dashboard_id)
    datasets: dict[UUID, tuple[int, Fields | None, str]] = {}
    dataset_out: list[dict[str, Any]] = []
    for reference in dashboard.datasets[:10]:
        try:
            _, dataset = await _dataset(user, reference.dataset_id, reference.revision)
        except (NovaException, Refusal):
            datasets[reference.dataset_id] = (reference.revision, None, "Unavailable dataset")
            dataset_out.append({"dataset_id": str(reference.dataset_id), "available": False})
            continue
        fields = Fields(dataset)
        datasets[reference.dataset_id] = (reference.revision, fields, dataset.title)
        dataset_out.append({
            "dataset_id": str(reference.dataset_id), "title": dataset.title,
            "fields": _field_summary(dataset),
        })
    chart_reads = 0
    pages: list[dict[str, Any]] = []
    for page in dashboard.pages:
        widgets: list[dict[str, Any]] = []
        for widget in page.widgets[:60]:
            entry: dict[str, Any] = {
                "widget_id": str(widget.widget_id), "kind": widget.kind,
                "position": widget.layout.model_dump(),
            }
            if widget.title:
                entry["title"] = widget.title
            if widget.widget_id == selected:
                entry["selected"] = True
            if isinstance(widget, ChartWidget) and chart_reads < MAX_CHART_READS:
                chart_reads += 1
                try:
                    snapshot = await _documents().read(
                        user, widget.chart_ref.chart_id, draft=editable,
                        revision=widget.chart_ref.revision,
                    )
                    chart = snapshot["definition"]
                    if isinstance(chart, ChartDefinition):
                        known = datasets.get(chart.dataset_id)
                        entry.update(_chart_summary(chart, known[1] if known else None))
                        if known:
                            entry["dataset"] = known[2]
                except NovaException:
                    entry["chart_title"] = "Chart details unavailable"
            widgets.append(entry)
        pages.append({"page_id": str(page.page_id), "title": page.title, "widgets": widgets})
    filters: list[dict[str, Any]] = []
    for item in dashboard.filters[:32]:
        described: dict[str, Any] = {"title": item.title, "kind": item.kind,
                                     "scope": item.scope.kind}
        if item.field is not None:
            known = datasets.get(item.field.dataset_id)
            described["field"] = known[1].name(item.field.field_id) if known and known[1] \
                else item.field.field_id
        filters.append(described)
    return {
        "dashboard_id": str(dashboard_id), "title": dashboard.title, "revision": revision,
        "editable": editable, "published": published, "pages": pages,
        "datasets": dataset_out, "filters": filters,
    }


# ── Values and placement ─────────────────────────────────────────────


def scalar(value: Any, field: DatasetField) -> Any:
    """A friendly value as the typed value the field declares."""
    kind = field.logical_type
    try:
        if kind == "string":
            return StringValue(value=str(value))
        if kind == "boolean":
            if isinstance(value, bool):
                return BooleanValue(value=value)
            if str(value).strip().lower() in {"true", "false"}:
                return BooleanValue(value=str(value).strip().lower() == "true")
        elif isinstance(value, bool):
            pass
        elif kind == "integer":
            number = Decimal(str(value))
            if number == number.to_integral_value():
                return IntegerValue(value=str(int(number)))
        elif kind == "decimal":
            return DecimalValue(value=format(Decimal(str(value)).normalize(), "f"))
        elif kind == "float":
            return FloatValue(value=float(value))
        elif kind == "date":
            return DateValue(value=str(value)[:10])
        elif kind == "timestamp":
            text = str(value).replace(" ", "T", 1)
            try:
                return TimestampValue(value=text)
            except ValidationError:
                # A time without a zone is read as UTC, the dataset's stated zone.
                return TimestampValue(value=f"{text}Z")
    except (ValidationError, InvalidOperation, ValueError, TypeError):
        pass
    raise Refusal(f"'{value}' is not a valid value for '{field.name}' ({kind}).")


def plain(value: Any) -> Any:
    """A typed result value as JSON the answer can quote exactly."""
    if value is None:
        return None
    raw = value.value
    if value.type == "integer":
        return int(raw)
    if value.type == "decimal":
        number = Decimal(raw)
        as_float = float(number)
        return as_float if Decimal(repr(as_float)) == number else raw
    return raw


def first_free(occupied: list[Layout], width: int, height: int) -> Layout:
    """The top-left-most free slot on the 12-column grid."""
    bottom = max((item.y + item.h for item in occupied), default=0)
    for y in range(0, bottom + 1):
        for x in range(0, 13 - width):
            candidate = Layout(x=x, y=y, w=width, h=height)
            if not any(candidate.overlaps(item) for item in occupied):
                return candidate
    return Layout(x=0, y=bottom, w=width, h=height)


def build_chart(
    spec: ChartSpec, *, chart_id: UUID, dataset_id: UUID, revision: int, dataset: Dataset,
) -> ChartDefinition:
    """A chart definition from friendly arguments, validated against its dataset."""
    fields = Fields(dataset)
    visual = spec.visual
    series = cast(Literal["bar", "line", "area"], visual if visual in {"line", "area"} else "bar")
    dims = [fields.dimension(item) for item in spec.dimensions or []]
    measures = [fields.measure(item, series) for item in spec.measures or []]
    encoding: Any
    if visual in {"bar", "line", "area"}:
        if not dims or not measures:
            raise Refusal(f"A {VISUAL_WORDS[visual]} needs a dimension and a measure.")
        color = fields.dimension(DimensionArg(field=spec.color)) if spec.color else None
        if color is not None and any(item.field_id == color.field_id for item in dims):
            # Coloring by the axis field would turn every point into its own series.
            color = None
        encoding = CartesianEncoding(
            dimensions=tuple(dims), measures=tuple(measures),
            color=color,
            orientation=(spec.orientation or "vertical") if visual == "bar" else "vertical",
            stack=cast(Any, spec.stack or "none"),
        )
    elif visual == "pie":
        if len(dims) != 1 or len(measures) != 1:
            raise Refusal("A pie chart needs exactly one dimension and one measure.")
        # As in the chart builder: a capped pie combines the rest into Other
        # when the rest is exact (sum and count).
        additive = measures[0].aggregation in ("sum", "count")
        encoding = CategoryEncoding(category=dims[0], measure=measures[0],
                                    donut=bool(spec.donut),
                                    overflow="other" if additive else "error")
    elif visual == "kpi":
        if len(measures) != 1 or dims:
            raise Refusal("A KPI shows exactly one measure and no dimension.")
        encoding = SingleMeasureEncoding(measure=measures[0])
    elif visual == "table":
        if not dims and not measures:
            raise Refusal("A table needs at least one field.")
        encoding = TableEncoding(dimensions=tuple(dims), measures=tuple(measures),
                                 grand_total=bool(spec.totals))
    elif visual == "pivot":
        across = [fields.dimension(item) for item in spec.columns or []]
        if not dims or not across or not measures:
            raise Refusal("A pivot table needs row fields, column fields (columns) and a measure.")
        encoding = TableEncoding(dimensions=tuple(dims), pivot_columns=tuple(across),
                                 measures=tuple(measures), grand_total=bool(spec.totals))
    else:
        if not spec.x or not spec.y:
            raise Refusal("A scatter plot needs two number fields, x and y.")
        encoding = ScatterEncoding(
            x=fields.numeric(spec.x), y=fields.numeric(spec.y),
            size=fields.numeric(spec.size) if spec.size else None,
            color=fields.dimension(DimensionArg(field=spec.color)) if spec.color else None,
        )
    sort: tuple[ChartSort, ...] = ()
    if spec.sort is not None:
        identity = fields.resolve(spec.sort.field)
        encoded = next((item for item in measures if item.field_id == identity), None)
        sort = (ChartSort(field_id=identity, direction=spec.sort.direction,
                          aggregation=encoded.aggregation if encoded else None),)
    elif visual in {"bar", "pie"} and measures and dims and dims[0].date_grain is None:
        sort = (ChartSort(field_id=measures[0].field_id, direction="desc",
                          aggregation=measures[0].aggregation),)
    style = ChartStyle()
    if spec.shade:
        if visual not in {"table", "pivot"}:
            raise Refusal("Shading applies to tables and pivot tables.")
        shaded = {fields.resolve(name) for name in spec.shade}
        measured = {item.field_id for item in measures}
        if not shaded <= measured:
            raise Refusal("Shade only measures the table shows.")
        style = ChartStyle(conditional_formats=tuple(
            ConditionalFormat(field_id=field_id, kind="scale") for field_id in sorted(shaded)))
    try:
        chart = ChartDefinition(
            chart_id=chart_id, title=spec.title, dataset_id=dataset_id,
            dataset_revision=revision, visual=visual, encoding=encoding, sort=sort,
            style=style,
            # The chart builder saves this too; both show the first rows with a warning.
            result_overflow="truncate_with_warning",
        )
    except ValidationError as exc:
        reason = exc.errors(include_input=False)[0].get("msg", "invalid chart")
        raise Refusal(f"The chart is not valid: {str(reason).removeprefix('Value error, ')}.") \
            from None
    if isinstance(dataset, DatasetDefinition):
        from app.modules.viz.bindings import validate_chart_binding

        try:
            validate_chart_binding(chart, dataset)
        except ValueError as exc:
            raise Refusal(f"The chart does not fit its dataset: {exc}.") from None
    return chart


def spec_from_chart(chart: ChartDefinition) -> dict[str, Any]:
    """The editable arguments of a chart the assistant can rebuild."""
    encoding = chart.encoding

    def dim(item: Dimension) -> dict[str, Any]:
        return {"field": item.field_id, "date_grain": item.date_grain}

    def meas(item: Measure) -> dict[str, Any]:
        if item.aggregation not in {"sum", "avg", "min", "max", "count", "count_distinct"}:
            raise Refusal("This chart uses a calculation that can only be edited in the editor.")
        return {"field": item.field_id, "aggregation": item.aggregation}

    spec: dict[str, Any] = {"title": chart.title, "visual": chart.visual}
    if chart.visual in {"bar", "line", "area"} and isinstance(encoding, CartesianEncoding):
        spec.update(dimensions=[dim(item) for item in encoding.dimensions],
                    measures=[meas(item) for item in encoding.measures],
                    color=encoding.color.field_id if encoding.color else None,
                    orientation=encoding.orientation, stack=encoding.stack)
    elif chart.visual == "pie" and isinstance(encoding, CategoryEncoding):
        spec.update(dimensions=[dim(encoding.category)], measures=[meas(encoding.measure)],
                    donut=encoding.donut)
    elif chart.visual == "kpi" and isinstance(encoding, SingleMeasureEncoding):
        spec.update(measures=[meas(encoding.measure)])
    elif chart.visual in {"table", "pivot"} and isinstance(encoding, TableEncoding):
        spec.update(dimensions=[dim(item) for item in encoding.dimensions],
                    measures=[meas(item) for item in encoding.measures],
                    totals=encoding.grand_total)
        if chart.visual == "pivot":
            spec["columns"] = [dim(item) for item in encoding.pivot_columns]
        scaled = [item.field_id for item in chart.style.conditional_formats if item.kind == "scale"]
        if any(item.kind == "rule" for item in chart.style.conditional_formats):
            raise Refusal("This table has highlight rules that can only be edited in the editor.")
        if scaled:
            spec["shade"] = scaled
    elif chart.visual == "scatter" and isinstance(encoding, ScatterEncoding):
        spec.update(x=encoding.x, y=encoding.y, size=encoding.size,
                    color=encoding.color.field_id if encoding.color else None)
    else:
        raise Refusal("This kind of chart can only be changed in the editor.")
    if chart.sort:
        spec["sort"] = {"field": chart.sort[0].field_id, "direction": chart.sort[0].direction}
    return spec


# ── Committing ───────────────────────────────────────────────────────


async def _receipt(user: dict[str, Any], operation_id: UUID) -> dict[str, Any] | None:
    try:
        return dict(await _documents().receipt(user, None, operation_id))
    except NovaException as exc:
        if exc.status_code == 404:
            return None
        raise


async def _settle(
    user: dict[str, Any], operation_id: UUID, command: Callable[[], Awaitable[Any]],
) -> dict[str, Any]:
    """Run one command; an unconfirmed outcome is resolved by its receipt."""
    try:
        return dict(await command())
    except NovaException as exc:
        if exc.status_code not in _UNCERTAIN:
            raise
    except Exception as exc:  # noqa: BLE001 - outcome unknown: ask the receipt
        logger.warning("Native dashboard command outcome unknown: %s", type(exc).__name__)
    receipt = await _receipt(user, operation_id)
    if receipt is None:
        raise Refusal(
            "The change could not be confirmed and was not saved. Nothing was changed; "
            "ask again to retry."
        )
    return receipt


def _attempts(base: UUID) -> list[UUID]:
    """The operation ids ``apply_changes`` uses for one logical change."""
    return [uuid5(base, f"apply:{attempt}") for attempt in (1, 2)]


async def already_applied(user: dict[str, Any], base: UUID) -> bool:
    """Whether the change ``apply_changes`` would make under ``base`` is committed."""
    return any([await _receipt(user, operation_id) is not None
                for operation_id in _attempts(base)])


async def apply_changes(
    user: dict[str, Any], dashboard_id: UUID, base: UUID,
    build: Callable[[DashboardDefinition], list[Any]],
) -> dict[str, Any]:
    """One dashboard operation under revision CAS; on a conflict, re-read and retry once.

    A retried tool call returns the receipt it already committed.
    """
    attempts = _attempts(base)
    for operation_id in attempts:
        prior = await _receipt(user, operation_id)
        if prior is not None:
            return prior
    first: DashboardDefinition | None = None
    for attempt, operation_id in enumerate(attempts, start=1):
        revision, dashboard = await _draft(user, dashboard_id)
        changes = build(dashboard)
        if first is None:
            first = dashboard
        elif _touched(first, changes, dashboard) != _touched(dashboard, changes, dashboard):
            # Someone changed what this change touches: never overwrite their edit.
            raise Refusal("Someone else changed that part of the dashboard at the same time. "
                          "Look at it again, then ask me to make the change.")
        operation = _validated(dashboard, operation_id, revision, changes)
        try:
            return await _settle(
                user, operation_id, partial(_documents().apply, user, dashboard_id, operation),
            )
        except NovaException as exc:
            if exc.status_code == 409 and attempt == 1:
                continue
            raise
    raise Refusal("The dashboard keeps changing while this is saved. Try again shortly.")


def _touched(
    dashboard: DashboardDefinition, changes: list[Any], current: DashboardDefinition,
) -> dict[str, Any]:
    """The state of what a change alters, to detect a concurrent edit of the same thing.

    A change naming a widget compares that whole widget. A placement compares
    only the layout of a widget it really moves in ``current``, so arranging the
    page does not collide with someone editing another widget's title or text.
    """
    named: set[str] = set()
    placed: set[str] = set()
    layouts = {str(widget.widget_id): widget.layout.model_dump(mode="json")
               for page in current.pages for widget in page.widgets}
    for change in changes:
        data = change.model_dump(mode="json") if hasattr(change, "model_dump") else dict(change)
        if data.get("widget_id"):
            named.add(str(data["widget_id"]))
        if isinstance(data.get("widget"), dict) and data["widget"].get("widget_id"):
            named.add(str(data["widget"]["widget_id"]))
        for placement in data.get("placements") or ():
            identity = str(placement.get("widget_id"))
            if layouts.get(identity) != placement.get("layout"):
                placed.add(identity)
    state: dict[str, Any] = {}
    for page in dashboard.pages:
        for widget in page.widgets:
            identity = str(widget.widget_id)
            if identity in named:
                state[identity] = widget.model_dump(mode="json")
            elif identity in placed:
                state[identity] = widget.layout.model_dump(mode="json")
    return state


def _validated(
    dashboard: DashboardDefinition, operation_id: UUID, revision: int, changes: list[Any],
) -> Operation:
    """The operation for ``changes``, checked against ``dashboard`` before it is sent."""
    try:
        operation = Operation(operation_id=operation_id, expected_revision=revision,
                              changes=tuple(changes))
    except ValidationError as exc:
        reason = exc.errors(include_input=False)[0].get("msg", "invalid change")
        raise Refusal(
            f"The dashboard cannot take this change: "
            f"{str(reason).removeprefix('Value error, ')}."
        ) from None
    _check_operation(dashboard, operation)
    return operation


def _check_operation(dashboard: DashboardDefinition, operation: Operation) -> None:
    """Apply locally first, so a refusal can say why in plain words."""
    from app.modules.viz.operations import apply_operation

    try:
        apply_operation(dashboard, operation)
    except ValidationError as exc:
        reason = exc.errors(include_input=False)[0].get("msg", "invalid change")
        raise Refusal(
            f"The dashboard cannot take this change: {str(reason).removeprefix('Value error, ')}."
        ) from None
    except ValueError as exc:
        raise Refusal(f"The dashboard cannot take this change: {exc}.") from None


def _changed(dashboard_id: UUID, receipt: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"dashboard_id": str(dashboard_id), "revision": int(receipt["new_revision"]), **extra}


def _outcome(summary: str, data: dict[str, Any], change: dict[str, Any]) -> ToolOutcome:
    return ToolOutcome(
        ok=True, summary=summary, data={**data, **change},
        metadata={"native_dashboard_change": change},
        trace_detail={"native_dashboard_change": change},
    )


# ── Naming widgets for approvals and results ─────────────────────────

_KIND_WORDS = {"chart": "chart", "text": "text box", "image": "image",
               "filter": "filter control", "spacer": "spacer"}


async def _charts(user: dict[str, Any], widgets: list[Any]) -> dict[UUID, ChartDefinition]:
    """The draft chart behind each chart widget, read as the caller (bounded)."""
    charts: dict[UUID, ChartDefinition] = {}
    for widget in [item for item in widgets if isinstance(item, ChartWidget)][
            :MAX_PREVIEW_CHART_READS]:
        try:
            snapshot = await _documents().read(user, widget.chart_ref.chart_id, draft=True,
                                               revision=widget.chart_ref.revision)
        except NovaException:
            continue
        if isinstance(snapshot["definition"], ChartDefinition):
            charts[widget.widget_id] = snapshot["definition"]
    return charts


def _widget_name(widget: Any, charts: dict[UUID, ChartDefinition]) -> str:
    """What the person sees on the widget: its title, else its chart's, else its kind."""
    chart = charts.get(widget.widget_id)
    title = widget.title or (chart.title if chart is not None else "")
    if title:
        return f"'{title[:60]}'"
    return f"an untitled {_KIND_WORDS.get(widget.kind, 'widget')}"


def _bounded(items: list[str], noun: str = "widgets") -> str:
    """Up to ``MAX_LISTED`` items, then how many more."""
    text = "; ".join(items[:MAX_LISTED])
    if len(items) > MAX_LISTED:
        text += f"; and {len(items) - MAX_LISTED} more {noun}"
    return text


def _filter_reaches(item: FilterDefinition, chart: ChartDefinition, page: UUID,
                    widget: UUID) -> bool:
    """Whether a dashboard filter applies to one chart widget, as the dashboard applies it:
    in scope, and bound to the chart's own dataset revision or mapped to it."""
    scope = item.scope
    if scope.kind == "page" and scope.page_id != page:
        return False
    if scope.kind in {"widget_subset", "chart_local"} and widget not in scope.widget_ids:
        return False

    def same(binding: FieldBinding) -> bool:
        return (binding.dataset_id, binding.dataset_revision) == (
            chart.dataset_id, chart.dataset_revision)

    return bool((item.field is not None and same(item.field))
                or any(same(mapping.target) for mapping in item.mappings))


async def _filter_targets(
    user: dict[str, Any], dashboard: DashboardDefinition, filters: list[FilterDefinition],
) -> list[list[str]]:
    """For each filter, the titles of the chart widgets it filters."""
    placed = [(page.page_id, widget) for page in dashboard.pages for widget in page.widgets]
    charts = await _charts(user, [widget for _, widget in placed])
    targets: list[list[str]] = []
    for item in filters:
        names = []
        for page_id, widget in placed:
            chart = charts.get(widget.widget_id)
            if chart is not None and _filter_reaches(item, chart, page_id, widget.widget_id):
                names.append(_widget_name(widget, charts).strip("'"))
        targets.append(names)
    return targets


# ── Tools ────────────────────────────────────────────────────────────


class _Tool:
    nested_requests_measured = True
    classification: ToolClassification = "read_only"
    requires_consent = False
    name = ""
    description = ""
    args_model: type[_Args] = _Args
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

    def preview(self, invocation: ToolInvocation) -> str:
        return "Read the dashboard"

    async def consent_preview(self, invocation: ToolInvocation, context: Any) -> str:
        """The approval text, from the dashboard as it is now and checked like the change.

        Read-only, as the caller. Without a readable dashboard it is ``preview``.
        """
        plain = self.preview(invocation)
        user = _user(context)
        if not user:
            return plain
        try:
            args = _parse(self.args_model, invocation.arguments)
            return await self.describe(args, invocation, context, user) or plain
        except Refusal as exc:
            return f"{plain}. This cannot be saved as proposed: {exc}"
        except (NovaException, ValidationError):
            return plain

    async def describe(
        self, args: Any, invocation: ToolInvocation, context: Any, user: dict[str, Any],
    ) -> str | None:
        """A fuller approval text from the current dashboard; ``None`` keeps ``preview``."""
        return None

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        user = _user(context)
        if not user:
            return _fail("A signed-in person is required.")
        try:
            args = _parse(self.args_model, invocation.arguments)
            return await self.act(args, invocation, context, user)
        except Refusal as exc:
            # A refusal names what to change; access and engine failures below do not.
            return _fail(str(exc), recoverable=True)
        except NovaException as exc:
            return _fail(_explain(exc))
        except ValidationError:
            # Never echo the definition: its values may be private.
            return _fail("That request could not be turned into a valid dashboard change.")

    async def act(
        self, args: Any, invocation: ToolInvocation, context: Any, user: dict[str, Any],
    ) -> ToolOutcome:
        raise NotImplementedError


class _Change(_Tool):
    classification: ToolClassification = "session_change"
    #: Every native dashboard change is approved by the person first.
    requires_consent = True


_DIM = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "maxLength": 256},
        "date_grain": {
            "type": "string", "enum": ["day", "week", "month", "quarter", "year"],
            "description": "Only for date fields; leave it out for any other field.",
        },
    },
    "required": ["field"],
    "additionalProperties": False,
}
_MEASURE = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "maxLength": 256,
                  "description": "Leave out only to count rows."},
        "aggregation": {"type": "string", "enum": ["sum", "avg", "min", "max", "count",
                                                   "count_distinct"]},
    },
    "additionalProperties": False,
}
_SORT = {
    "type": "object",
    "properties": {"field": {"type": "string", "maxLength": 256},
                   "direction": {"type": "string", "enum": ["asc", "desc"]}},
    "required": ["field"],
    "additionalProperties": False,
}
_UUID = {"type": "string", "format": "uuid"}
_SHAPE = {
    "dimensions": {"type": "array", "items": _DIM, "maxItems": 4,
                   "description": "Fields to group by (bar/line/area/pie/table)."},
    "measures": {"type": "array", "items": _MEASURE, "maxItems": 4,
                 "description": "Numbers to show (bar/line/area/pie/kpi/table)."},
    "x": {"type": "string", "description": "Scatter: number field across."},
    "y": {"type": "string", "description": "Scatter: number field up."},
    "size": {"type": "string", "description": "Scatter: optional number field for size."},
    "color": {"type": "string", "description": "Optional field that splits series by color."},
    "orientation": {"type": "string", "enum": ["vertical", "horizontal"]},
    "stack": {"type": "string", "enum": list(STACKS)},
    "donut": {"type": "boolean"},
    "sort": _SORT,
    "columns": {"type": "array", "items": _DIM, "maxItems": 3,
                "description": "Pivot only: fields across the top; dimensions are the rows."},
    "totals": {"type": "boolean", "description": "Table or pivot: show totals."},
    "shade": {"type": "array", "items": {"type": "string", "maxLength": 256}, "maxItems": 8,
              "description": "Table or pivot: measures to shade with a colour scale."},
}
_VISUAL = {"type": "string", "enum": list(VISUAL_WORDS)}


class ListDashboardsTool(_Tool):
    name = "native_dashboard_list"
    args_model = ListArgs
    description = (
        "List the Dataviz dashboards your active role can open, across your workspaces: title, "
        "dashboard_id, workspace, draft revision (when you can edit), and whether it is "
        "published. Use it to find a dashboard by name."
    )
    parameters = {
        "type": "object",
        "properties": {"search": {"type": "string", "maxLength": 120,
                                  "description": "Optional words in the title."}},
        "additionalProperties": False,
    }

    async def act(self, args: ListArgs, invocation, context, user) -> ToolOutcome:
        found: dict[str, dict[str, Any]] = {}
        spaces = (await _spaces(user))[:MAX_WORKSPACES]
        needle = (args.search or "").casefold()
        for space in spaces:
            for draft in (True, False):
                try:
                    rows = await _documents().browse(user, space.id, kind="dashboard",
                                                     draft=draft, limit=MAX_DASHBOARDS)
                except NovaException:
                    continue
                for row in rows:
                    title = str(row.get("title") or "Untitled dashboard")
                    if needle and needle not in title.casefold():
                        continue
                    key = str(row["resource_id"])
                    entry = found.setdefault(key, {
                        "dashboard_id": key, "title": title, "workspace": space.name,
                        "workspace_id": space.id, "can_edit": False, "draft_revision": None,
                        "published": row.get("current_publication_id") is not None,
                    })
                    if row.get("current_publication_id") is not None:
                        entry["published"] = True
                    if draft:
                        entry.update(title=title, can_edit=True, draft_revision=row["revision"])
        dashboards = list(found.values())[:MAX_DASHBOARDS]
        return ToolOutcome(ok=True, summary=f"{len(dashboards)} dashboards",
                           data={"dashboards": dashboards})


class ReadDashboardTool(_Tool):
    name = "native_dashboard_read"
    args_model = DashboardArgs
    description = (
        "Read a Dataviz dashboard (the open one when dashboard_id is left out): its title, "
        "revision, pages, widgets with their chart titles, chart types, fields and grid "
        "positions (12 columns), its datasets with field names and types, and its filters. "
        "Read it before changing the dashboard."
    )
    parameters = {"type": "object", "properties": {"dashboard_id": _UUID},
                  "additionalProperties": False}

    async def act(self, args: DashboardArgs, invocation, context, user) -> ToolOutcome:
        identity = _dashboard_id(args.dashboard_id, context)
        opened = _open(context)
        selected = _uuid(opened.get("selected_widget_id")) \
            if _uuid(opened.get("dashboard_id")) == identity else None
        summary = await dashboard_summary(user, identity, selected=selected)
        return ToolOutcome(ok=True, summary=f"Dashboard '{summary['title']}'", data=summary)


class DescribeDatasetTool(_Tool):
    name = "native_dataset_describe"
    args_model = DescribeArgs
    description = (
        "Describe one dataset's fields: name, type (string, integer, decimal, date...), and "
        "whether it is a dimension or a measure, plus saved metrics. Without a revision it uses "
        "the version the open dashboard pins."
    )
    parameters = {
        "type": "object",
        "properties": {"dataset_id": _UUID, "revision": {"type": "integer", "minimum": 1}},
        "required": ["dataset_id"],
        "additionalProperties": False,
    }

    async def act(self, args: DescribeArgs, invocation, context, user) -> ToolOutcome:
        # The open dashboard's pin wins: a model can confuse dashboard and dataset revisions.
        revision = await _pinned(user, context, args.dataset_id) or args.revision
        revision, dataset = await _dataset(user, args.dataset_id, revision)
        return ToolOutcome(
            ok=True, summary=f"Dataset '{dataset.title}'",
            data={"dataset_id": str(args.dataset_id), "revision": revision,
                  "title": dataset.title, "fields": _field_summary(dataset)},
        )


async def _pinned(user: dict[str, Any], context: Any, dataset_id: UUID) -> int | None:
    """The revision the open dashboard pins for a dataset, if any."""
    identity = _uuid(_open(context).get("dashboard_id"))
    if identity is None:
        return None
    try:
        _, dashboard, _, _ = await _readable(user, identity)
    except (NovaException, Refusal):
        return None
    return next((item.revision for item in dashboard.datasets
                 if item.dataset_id == dataset_id), None)


class QueryDatasetTool(_Tool):
    name = "native_dataset_query"
    args_model = QueryArgs

    def preview(self, invocation: ToolInvocation) -> str:
        return "Query the dashboard data"
    #: Governed rows from the dashboard's own dataset answer a data question.
    may_settle = frozenset({"semantic_query"})
    description = (
        "Get numbers from a dashboard dataset as your active role: totals, rankings and "
        "breakdowns. Give dataset_id, measures (field + sum/avg/min/max/count/count_distinct), "
        "optional dimensions (a field, with date_grain for dates), filters (field, op, values) "
        "and sort. Use it to check numbers before charting them; quote numbers exactly."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dataset_id": _UUID,
            "revision": {"type": "integer", "minimum": 1},
            "dimensions": {"type": "array", "items": _DIM, "maxItems": 4},
            "measures": {"type": "array", "items": _MEASURE, "maxItems": 6},
            "filters": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {"type": "string", "enum": [
                            "eq", "neq", "in", "not_in", "lt", "lte", "gt", "gte", "between",
                            "is_null", "is_not_null"]},
                        "values": {"type": "array", "maxItems": 100,
                                   "items": {"type": ["string", "number", "boolean"]}},
                    },
                    "required": ["field"],
                    "additionalProperties": False,
                },
            },
            "sort": _SORT,
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_QUERY_ROWS},
        },
        "required": ["dataset_id"],
        "additionalProperties": False,
    }

    async def act(self, args: QueryArgs, invocation, context, user) -> ToolOutcome:
        from app.modules.viz.contracts.queries import (
            DatasetQueryRequest,
            QueryDimension,
            QueryMeasure,
            QuerySort,
        )

        # The open dashboard's pin wins: a model can confuse dashboard and dataset revisions.
        revision = await _pinned(user, context, args.dataset_id) or args.revision
        revision, dataset = await _dataset(user, args.dataset_id, revision)
        fields = Fields(dataset)
        dimensions = []
        for item in args.dimensions:
            dimension = fields.dimension(item)
            dimensions.append(QueryDimension(
                field_id=dimension.field_id,
                # Resolved by the field: dates default to month, other fields have none.
                date_grain=dimension.date_grain,
            ))
        measures = []
        for asked in args.measures:
            if asked.field is None:
                measures.append(QueryMeasure(aggregation="count"))
                continue
            if fields.resolve(asked.field) in fields.metrics:
                raise Refusal(f"'{asked.field}' is a saved metric; ask with its source fields.")
            measure = fields.measure(asked)
            measures.append(QueryMeasure(field_id=measure.field_id,
                                         aggregation=asked.aggregation))
        predicates = []
        for condition in args.filters:
            field = fields.field(condition.field)
            values = tuple(scalar(value, field) for value in condition.values)
            try:
                predicates.append(Predicate(
                    field=FieldBinding(dataset_id=args.dataset_id, dataset_revision=revision,
                                       field_id=field.field_id),
                    op=condition.op, values=values,
                ))
            except ValidationError:
                raise Refusal(
                    f"The filter on '{field.name}' needs a different number of values for "
                    f"'{condition.op}'."
                ) from None
        sort: tuple[QuerySort, ...] = ()
        if args.sort is not None:
            # "sum(revenue)" names the measure the way SQL does; sort by its field.
            asked_sort = re.sub(r"^\s*\w+\s*\(\s*(.+?)\s*\)\s*$", r"\1", args.sort.field)
            identity = fields.resolve(asked_sort)
            key = next((f"m{i}" for i, item in enumerate(measures) if item.field_id == identity),
                       None) or next((f"d{i}" for i, item in enumerate(dimensions)
                                      if item.field_id == identity), None)
            if key is None:
                raise Refusal("Sort by a field the question asks for.")
            sort = (QuerySort(column=key, direction=args.sort.direction),)
        elif measures and dimensions:
            temporal = fields.fields[dimensions[0].field_id].logical_type in TEMPORAL
            sort = (QuerySort(column="d0", direction="asc"),) if temporal else (
                QuerySort(column="m0", direction="desc"),)
        expression: Any = None
        if len(predicates) == 1:
            expression = predicates[0]
        elif predicates:
            expression = And(children=tuple(predicates))
        request = DatasetQueryRequest(
            revision=revision, draft=False, dimensions=tuple(dimensions),
            measures=tuple(measures),
            filters=FilterTree(expression=expression) if expression is not None else None,
            sort=sort, limit=args.limit,
        )
        try:
            result = await _datasets().query(user, args.dataset_id, request)
        except NovaException as exc:
            raise Refusal(_query_refusal(exc)) from None
        columns = [column.name for column in result.columns]
        rows = [[plain(value) for value in row] for row in result.rows]
        title = dataset.title
        measured = [
            f"{item.aggregation}({fields.name(item.field_id) if item.field_id else 'rows'})"
            for item in measures
        ]
        facts = {
            "dataset_id": str(args.dataset_id), "dataset": title, "revision": revision,
            "dimensions": [fields.name(item.field_id) for item in dimensions],
            "measures": measured,
            "filters": [f"{item.field} {item.op} {item.values}" for item in args.filters],
            "truncated": result.truncated,
        }
        return ToolOutcome(
            ok=True, summary=f"{title}: {len(rows)} rows",
            table={"title": title, "columns": columns, "rows": rows},
            data={"columns": columns, "rows": rows[:MAX_QUERY_ROWS], "facts": facts},
            evidence={"source": "dataviz_dataset", **facts},
            metadata={"settles_capabilities": ["semantic_query"]},
        )


def _dataset_for(spec_dataset: UUID | None, dashboard: DashboardDefinition) -> DatasetReference:
    pinned = {item.dataset_id: item for item in dashboard.datasets}
    if spec_dataset is not None:
        if spec_dataset not in pinned:
            raise Refusal("That dataset is not on this dashboard. Attach it first.")
        return pinned[spec_dataset]
    if len(pinned) == 1:
        return next(iter(pinned.values()))
    if not pinned:
        raise Refusal("This dashboard has no dataset yet. Attach one first.")
    raise Refusal("This dashboard has several datasets; name the dataset_id for each chart.")


def _page(dashboard: DashboardDefinition, page_id: UUID | None, context: Any) -> Page:
    wanted = page_id or _uuid(_open(context).get("page_id"))
    page = next((item for item in dashboard.pages if item.page_id == wanted), None)
    if page is None and page_id is not None:
        raise Refusal("That page is not on this dashboard.")
    return page or dashboard.pages[0]


class AddChartsTool(_Change):
    name = "native_dashboard_add_charts"
    args_model = AddChartsArgs
    description = (
        "Add one to six new charts to a Dataviz dashboard (the open one when dashboard_id is "
        "left out), each placed in the first free spot of the page. Each chart: title, visual "
        "(bar, line, area, pie, kpi, table, scatter), the dataset (dataset_id, needed when the "
        "dashboard has several), dimensions and measures by field name (scatter uses x and y), "
        "optional color, sort, orientation, stack, width (2-12 columns) and height. Needs the "
        "user's approval. Returns the saved revision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dashboard_id": _UUID,
            "page_id": _UUID,
            "charts": {
                "type": "array", "minItems": 1, "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 256},
                        "visual": _VISUAL,
                        "dataset_id": _UUID,
                        **_SHAPE,
                        "width": {"type": "integer", "minimum": 2, "maximum": 12},
                        "height": {"type": "integer", "minimum": 2, "maximum": 48},
                    },
                    "required": ["title", "visual"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["charts"],
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        charts = invocation.arguments.get("charts")
        names = [
            f"{VISUAL_WORDS.get(str(item.get('visual')), 'chart')} '{str(item.get('title'))[:80]}'"
            for item in (charts if isinstance(charts, list) else [])[:6] if isinstance(item, dict)
        ]
        return "Add to the dashboard: " + (", ".join(names) or "new charts")

    async def act(self, args: AddChartsArgs, invocation, context, user) -> ToolOutcome:
        identity = _dashboard_id(args.dashboard_id, context)
        base = _base(invocation)
        revision, dashboard = await _draft(user, identity)
        workspace = await _documents().workspace_of(user, identity)
        datasets: dict[tuple[UUID, int], Dataset] = {}
        charts: list[tuple[ChartSpec, ChartDefinition]] = []
        for index, spec in enumerate(args.charts):
            reference = _dataset_for(spec.dataset_id, dashboard)
            key = (reference.dataset_id, reference.revision)
            if key not in datasets:
                datasets[key] = (await _dataset(user, *key))[1]
            charts.append((spec, build_chart(
                spec, chart_id=uuid5(base, f"chart:{index}"), dataset_id=reference.dataset_id,
                revision=reference.revision, dataset=datasets[key],
            )))
        for index, (_, chart) in enumerate(charts):
            operation_id = uuid5(base, f"chart-create:{index}")
            await _settle(user, operation_id, partial(
                _documents().create_visual, user, workspace, chart, operation_id=operation_id))
        placed: list[dict[str, Any]] = []

        def build(current: DashboardDefinition) -> list[Any]:
            page = _page(current, args.page_id, context)
            occupied = [widget.layout for widget in page.widgets]
            placed.clear()
            changes: list[Any] = []
            for index, (spec, chart) in enumerate(charts):
                width, height = DEFAULT_SIZE.get(spec.visual, (6, 4))
                layout = first_free(occupied, spec.width or width, spec.height or height)
                occupied.append(layout)
                widget = ChartWidget(
                    widget_id=uuid5(base, f"widget:{index}"), layout=layout,
                    chart_ref=ChartReference(chart_id=chart.chart_id, revision=1),
                )
                changes.append(WidgetAdd(page_id=page.page_id, widget=widget))
                placed.append({"widget_id": str(widget.widget_id), "title": chart.title,
                               "visual": chart.visual, "page": page.title,
                               "position": layout.model_dump()})
            return changes

        receipt = await apply_changes(user, identity, base, build)
        change = _changed(identity, receipt)
        return _outcome(f"Added {len(placed)} chart(s) (version {change['revision']})",
                        {"added": placed, "title": dashboard.title, "base_revision": revision},
                        change)


class UpdateChartTool(_Change):
    name = "native_chart_update"
    args_model = UpdateChartArgs
    description = (
        "Change one chart on a Dataviz dashboard, named by its widget_id from "
        "native_dashboard_read: its title, visual (bar, line, area, pie, kpi, table, scatter), "
        "dimensions, measures (field + aggregation), color, sort, orientation or stack. Only the "
        "parts given change. Needs the user's approval. Returns the saved revision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dashboard_id": _UUID, "widget_id": _UUID,
            "title": {"type": "string", "minLength": 1, "maxLength": 256},
            "visual": _VISUAL, **_SHAPE,
        },
        "required": ["widget_id"],
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        changed = [key for key in ("title", "visual", "dimensions", "measures", "color", "sort",
                                   "orientation", "stack", "x", "y", "size", "donut",
                                   "columns", "totals", "shade")
                   if key in invocation.arguments]
        words = {"visual": "chart type", "dimensions": "grouping", "measures": "numbers",
                 "columns": "pivot columns", "shade": "shading"}
        parts = ", ".join(words.get(key, key) for key in changed) or "its settings"
        visual = invocation.arguments.get("visual")
        suffix = f" (to {VISUAL_WORDS.get(str(visual), 'chart')})" if visual else ""
        return f"Change a chart on the dashboard: {parts}{suffix}"

    async def act(self, args: UpdateChartArgs, invocation, context, user) -> ToolOutcome:
        identity = _dashboard_id(args.dashboard_id, context)
        base = _base(invocation)
        _, dashboard = await _draft(user, identity)
        widget = next((item for page in dashboard.pages for item in page.widgets
                       if item.widget_id == args.widget_id), None)
        if not isinstance(widget, ChartWidget):
            raise Refusal("That widget is not a chart on this dashboard. Read the dashboard first.")
        chart_id = widget.chart_ref.chart_id
        snapshot = await _documents().read(user, chart_id, draft=True)
        current = snapshot["definition"]
        if not isinstance(current, ChartDefinition):
            raise Refusal("That widget's chart could not be read.")
        chart_head = int(snapshot["revision"])
        given = args.model_dump(exclude_unset=True, exclude={"dashboard_id", "widget_id"})
        structural = set(given) - {"title"}
        if structural:
            spec_data = spec_from_chart(current)
            visual = given.get("visual", spec_data["visual"])
            if visual == "kpi" and "dimensions" not in given:
                spec_data.pop("dimensions", None)
            if visual == "pie" and "dimensions" not in given and spec_data.get("dimensions"):
                spec_data["dimensions"] = spec_data["dimensions"][:1]
                spec_data["measures"] = (spec_data.get("measures") or [])[:1]
            if visual != spec_data["visual"]:
                for key in ("orientation", "stack", "donut"):
                    spec_data.pop(key, None)
                if visual != "pivot":
                    spec_data.pop("columns", None)
                if visual not in {"table", "pivot"}:
                    spec_data.pop("totals", None)
                    spec_data.pop("shade", None)
            spec_data.update(given)
            spec = _parse(ChartSpec, {key: value for key, value in spec_data.items()
                                      if value is not None})
            dataset = (await _dataset(user, current.dataset_id, current.dataset_revision))[1]
            updated = build_chart(spec, chart_id=chart_id, dataset_id=current.dataset_id,
                                  revision=current.dataset_revision, dataset=dataset)
        else:
            updated = current.model_copy(update={"title": given["title"]})
        operation_id = uuid5(base, "chart-replace")
        chart_receipt = await _settle(user, operation_id, lambda: _documents().replace_visual(
            user, updated, operation_id=operation_id, expected_revision=chart_head))
        chart_revision = int(chart_receipt["new_revision"])

        def build(latest: DashboardDefinition) -> list[Any]:
            for page in latest.pages:
                for item in page.widgets:
                    if item.widget_id == args.widget_id and isinstance(item, ChartWidget):
                        replaced = item.model_copy(update={"chart_ref": ChartReference(
                            chart_id=chart_id, revision=chart_revision)})
                        return [WidgetReplace(page_id=page.page_id, widget=replaced)]
            raise Refusal("That chart was removed from the dashboard meanwhile.")

        receipt = await apply_changes(user, identity, base, build)
        change = _changed(identity, receipt)
        return _outcome(
            f"Updated chart '{updated.title}' (version {change['revision']})",
            {"widget_id": str(args.widget_id), "title": updated.title,
             "visual": updated.visual}, change,
        )


class ArrangeTool(_Change):
    name = "native_dashboard_arrange"
    args_model = ArrangeArgs
    description = (
        "Move, resize or remove widgets on a Dataviz dashboard page. The grid has 12 columns: "
        "x is the column (0-11), y the row, w the width (2-12), h the height (2-48). Give only "
        "what changes for each widget_id from native_dashboard_read; widgets may not overlap. "
        "Needs the user's approval. Returns the saved revision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dashboard_id": _UUID,
            "move": {
                "type": "array", "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "widget_id": _UUID,
                        "x": {"type": "integer", "minimum": 0, "maximum": 11},
                        "y": {"type": "integer", "minimum": 0, "maximum": 10000},
                        "w": {"type": "integer", "minimum": 2, "maximum": 12},
                        "h": {"type": "integer", "minimum": 2, "maximum": 48},
                    },
                    "required": ["widget_id"],
                    "additionalProperties": False,
                },
            },
            "remove": {"type": "array", "items": _UUID, "maxItems": 20},
        },
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        move = invocation.arguments.get("move")
        remove = invocation.arguments.get("remove")
        def items(count: int) -> str:
            return "one item" if count == 1 else f"{count} items"

        parts = []
        if isinstance(move, list) and move:
            parts.append(f"move or resize {items(len(move))}")
        if isinstance(remove, list) and remove:
            parts.append(f"remove {items(len(remove))}")
        text = " and ".join(parts) or "rearrange the layout"
        return f"Dashboard layout: {text}"

    @staticmethod
    def _changes(args: ArrangeArgs, dashboard: DashboardDefinition) -> tuple[Page, list[Any]]:
        """The page the named widgets are on and the changes, checked as the editor would."""
        named = {item.widget_id for item in args.move} | set(args.remove)
        pages = [page for page in dashboard.pages
                 if named & {widget.widget_id for widget in page.widgets}]
        if len(pages) != 1:
            raise Refusal("Name widgets from one page of this dashboard.")
        page = pages[0]
        present = {widget.widget_id: widget for widget in page.widgets}
        missing = named - set(present)
        if missing:
            raise Refusal(
                "Some of those widgets are not on the page. Read the dashboard again."
            )
        changes: list[Any] = [WidgetRemove(page_id=page.page_id, widget_id=widget_id)
                              for widget_id in args.remove]
        if args.move:
            moves = {item.widget_id: item for item in args.move}
            placements = []
            for widget_id, widget in present.items():
                if widget_id in set(args.remove):
                    continue
                layout = widget.layout
                move = moves.get(widget_id)
                if move is not None:
                    if widget.locked:
                        raise Refusal("A locked widget cannot be moved; unlock it first.")
                    updates = {key: getattr(move, key) for key in ("x", "y", "w", "h")
                               if getattr(move, key) is not None}
                    try:
                        layout = Layout(**{**layout.model_dump(), **updates})
                    except ValidationError:
                        raise Refusal(
                            "A widget would extend past the 12 columns; make it narrower "
                            "or move it left."
                        ) from None
                placements.append(Placement(widget_id=widget_id, layout=layout))
            for index, item in enumerate(placements):
                if any(item.layout.overlaps(other.layout) for other in placements[:index]):
                    raise Refusal("Widgets would overlap there. Choose free positions.")
            changes.append(LayoutChange(page_id=page.page_id, placements=tuple(placements)))
        return page, changes

    async def describe(self, args: ArrangeArgs, invocation, context, user) -> str:
        """Each widget the change touches, by name, and what happens to it."""
        identity = _dashboard_id(args.dashboard_id, context)
        revision, dashboard = await _draft(user, identity)
        page, changes = self._changes(args, dashboard)
        # The same local check the change runs before it is sent.
        _validated(dashboard, uuid5(_base(invocation), "preview"), revision, changes)
        present = {widget.widget_id: widget for widget in page.widgets}
        touched = [present[item.widget_id] for item in args.move]
        touched += [present[widget_id] for widget_id in args.remove]
        charts = await _charts(user, touched[:MAX_LISTED])
        lines = []
        for move in args.move:
            widget = present[move.widget_id]
            old = widget.layout
            new = Layout(**{**old.model_dump(), **{
                key: getattr(move, key) for key in ("x", "y", "w", "h")
                if getattr(move, key) is not None}})
            what = []
            if (new.x, new.y) != (old.x, old.y):
                what.append(f"moved to column {new.x + 1}, row {new.y + 1}")
            if (new.w, new.h) != (old.w, old.h):
                what.append(f"resized to {new.w} columns wide and {new.h} rows high")
            lines.append(f"{_widget_name(widget, charts)} "
                         + (" and ".join(what) or "stays where it is"))
        lines += [f"{_widget_name(present[widget_id], charts)} removed"
                  for widget_id in args.remove]
        return f"Dashboard layout on page '{page.title[:60]}': {_bounded(lines)}"

    async def act(self, args: ArrangeArgs, invocation, context, user) -> ToolOutcome:
        identity = _dashboard_id(args.dashboard_id, context)

        def build(dashboard: DashboardDefinition) -> list[Any]:
            return self._changes(args, dashboard)[1]

        receipt = await apply_changes(user, identity, _base(invocation), build)
        change = _changed(identity, receipt)
        return _outcome(
            f"Arranged the dashboard (version {change['revision']})",
            {"moved": len(args.move), "removed": len(args.remove)}, change,
        )


class FiltersTool(_Change):
    name = "native_dashboard_filters"
    args_model = FiltersArgs
    description = (
        "Add dashboard filters (mode 'add', the default; a filter on the same field is "
        "replaced) or replace them all (mode 'replace'; an empty list clears them). Each filter: "
        "title, field, kind (multi_value, single_value, date_range, number_range_slider, "
        "text_entry, boolean_switch), optional dataset_id, default values, and scope (global "
        "or page). Needs the user's approval. Returns the saved revision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dashboard_id": _UUID,
            "mode": {"type": "string", "enum": ["add", "replace"]},
            "filters": {
                "type": "array", "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 256},
                        "field": {"type": "string"},
                        "dataset_id": _UUID,
                        "kind": {"type": "string", "enum": [
                            "multi_value", "single_value", "date_range", "number_range_slider",
                            "text_entry", "boolean_switch"]},
                        "defaults": {"type": "array", "maxItems": 50,
                                     "items": {"type": ["string", "number", "boolean"]}},
                        "scope": {"type": "string", "enum": ["global", "page"]},
                        "page_id": _UUID,
                    },
                    "required": ["title", "field"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        filters = invocation.arguments.get("filters")
        titles = [f"'{str(item.get('title'))[:60]}'" for item in
                  (filters if isinstance(filters, list) else []) if isinstance(item, dict)]
        if invocation.arguments.get("mode") == "replace":
            return "Replace the dashboard's filters with: " + (", ".join(titles) or "none")
        return "Add dashboard filters: " + ", ".join(titles)

    @staticmethod
    async def _resolve(
        args: FiltersArgs, context: Any, user: dict[str, Any],
    ) -> tuple[UUID, int, DashboardDefinition, dict[tuple[UUID, int], Dataset],
               list[tuple[FilterArg, DatasetReference, DatasetField]]]:
        """The dashboard draft and each filter's field in the dataset it is pinned to."""
        identity = _dashboard_id(args.dashboard_id, context)
        revision, dashboard = await _draft(user, identity)
        datasets: dict[tuple[UUID, int], Dataset] = {}
        resolved: list[tuple[FilterArg, DatasetReference, DatasetField]] = []
        for item in args.filters:
            reference = _dataset_for(item.dataset_id, dashboard)
            key = (reference.dataset_id, reference.revision)
            if key not in datasets:
                datasets[key] = (await _dataset(user, *key))[1]
            field = Fields(datasets[key]).field(item.field)
            resolved.append((item, reference, field))
        return identity, revision, dashboard, datasets, resolved

    @staticmethod
    def _build(
        args: FiltersArgs, base: UUID, context: Any, current: DashboardDefinition,
        datasets: dict[tuple[UUID, int], Dataset],
        resolved: list[tuple[FilterArg, DatasetReference, DatasetField]],
    ) -> tuple[list[Any], list[FilterDefinition]]:
        """The change and the filters it creates, checked against their fields."""
        created: list[FilterDefinition] = []
        for index, (item, reference, field) in enumerate(resolved):
            binding = FieldBinding(dataset_id=reference.dataset_id,
                                   dataset_revision=reference.revision,
                                   field_id=field.field_id)
            page = _page(current, item.page_id, context) if item.scope == "page" else None
            try:
                created.append(FilterDefinition(
                    filter_id=uuid5(base, f"filter:{index}"), title=item.title,
                    kind=item.kind, field=binding,
                    scope=FilterScope(kind=item.scope,
                                      page_id=page.page_id if page else None),
                    defaults=tuple(scalar(value, field) for value in item.defaults),
                ))
            except ValidationError as exc:
                reason = exc.errors(include_input=False)[0].get("msg", "invalid filter")
                raise Refusal(f"The filter '{item.title}' is not valid: "
                              f"{str(reason).removeprefix('Value error, ')}.") from None
        kept: list[FilterDefinition] = []
        if args.mode == "add":
            bound = {(item.field.dataset_id, item.field.field_id) for item in created
                     if item.field is not None}
            kept = [item for item in current.filters if item.field is None
                    or (item.field.dataset_id, item.field.field_id) not in bound]
            # A dropped filter must not leave a dependent behind.
            ids = {item.filter_id for item in kept}
            kept = [item.model_copy(update={"depends_on": tuple(
                parent for parent in item.depends_on if parent in ids)}) for item in kept]
        final = (*kept, *created)
        _check_controls(current, final, datasets)
        return [FiltersReplace(filters=final)], created

    async def describe(self, args: FiltersArgs, invocation, context, user) -> str:
        """Each filter with its field and the widgets it will filter, by name."""
        _, revision, dashboard, datasets, resolved = await self._resolve(args, context, user)
        base = _base(invocation)
        changes, created = self._build(args, base, context, dashboard, datasets, resolved)
        _validated(dashboard, uuid5(base, "preview"), revision, changes)
        targets = await _filter_targets(user, dashboard, created)
        lines = []
        for (item, _, field), names in zip(resolved, targets, strict=True):
            quoted = ["'" + name[:60] + "'" for name in names]
            reach = (f"filters {_bounded(quoted, 'charts')}" if names
                     else "filters no widget on the dashboard yet")
            lines.append(f"'{item.title[:60]}' on {field.name[:60]}, {reach}")
        if args.mode == "replace":
            return ("Replace the dashboard's filters with: " + "; ".join(lines)
                    if lines else "Remove all of the dashboard's filters")
        return "Add dashboard filters: " + "; ".join(lines)

    async def act(self, args: FiltersArgs, invocation, context, user) -> ToolOutcome:
        identity, _, _, datasets, resolved = await self._resolve(args, context, user)
        base = _base(invocation)
        saved: dict[str, Any] = {}

        def build(current: DashboardDefinition) -> list[Any]:
            changes, created = self._build(args, base, context, current, datasets, resolved)
            saved.update(dashboard=current, created=created)
            return changes

        receipt = await apply_changes(user, identity, base, build)
        change = _changed(identity, receipt)
        targets: list[list[str]] = [[] for _ in args.filters]
        if saved:
            # Already saved: naming the widgets is a courtesy that cannot undo it.
            with contextlib.suppress(NovaException):
                targets = await _filter_targets(user, saved["dashboard"], saved["created"])
        reach = [f"'{item.title}' filters " + (", ".join(names[:MAX_LISTED]) or "no widget yet")
                 for item, names in zip(args.filters, targets, strict=True)]
        return _outcome(
            f"Saved dashboard filters (version {change['revision']}). " + "; ".join(reach),
            {"filters": [{"title": item.title, "applies_to": names}
                         for item, names in zip(args.filters, targets, strict=True)],
             "mode": args.mode}, change,
        )


def _check_controls(
    dashboard: DashboardDefinition, filters: tuple[FilterDefinition, ...],
    datasets: dict[tuple[UUID, int], Dataset],
) -> None:
    from app.modules.viz.bindings import validate_document_controls

    full = {key: value for key, value in datasets.items() if isinstance(value, DatasetDefinition)}
    if len(full) != len(datasets):
        return
    try:
        candidate = dashboard.model_copy(update={"filters": filters})
        validate_document_controls(candidate, full)
    except (ValueError, KeyError) as exc:
        raise Refusal(f"The filter does not fit its field: {exc}.") from None


async def _table_dataset(
    user: dict[str, Any], dataset_id: UUID, choice: DatasetChoice,
) -> Any:
    """Inspect a table as the caller and describe the dataset to create from it."""
    from app.modules.viz.contracts.authoring import (
        ObjectDatasetUpdate,
        ObjectInspect,
        ObjectInspection,
        ObjectSelection,
    )

    assert choice.table is not None
    try:
        selection = ObjectSelection(
            kind=choice.table.kind, database=choice.table.database,
            object_name=choice.table.table,
            columns=tuple(choice.table.columns) if choice.table.columns else None,
        )
    except ValidationError:
        raise Refusal("Name the table's database, table and columns plainly.") from None
    inspected = await _datasets().inspect(user, ObjectInspect(dataset_id=dataset_id,
                                                              source=selection))
    if not isinstance(inspected, ObjectInspection):
        raise Refusal("That source cannot be used as a table dataset.")
    return ObjectDatasetUpdate(
        dataset_id=dataset_id, source=selection,
        title=choice.dataset_title or choice.table.table,
        expected_physical_id=inspected.source.physical_id,
        expected_schema_fingerprint=inspected.schema_fingerprint,
    )


async def _attach_table(
    user: dict[str, Any], dashboard_id: UUID, base: UUID, choice: DatasetChoice,
) -> dict[str, Any]:
    from app.modules.viz.contracts.authoring import DashboardDatasetCreate

    attempts = [uuid5(base, f"attach:{attempt}") for attempt in (1, 2)]
    for operation_id in attempts:
        prior = await _receipt(user, operation_id)
        if prior is not None:
            return prior
    request = DashboardDatasetCreate(
        dataset=await _table_dataset(user, uuid5(base, "dataset"), choice),
    )
    for attempt, operation_id in enumerate(attempts, start=1):
        revision, _ = await _draft(user, dashboard_id)
        try:
            return await _settle(user, operation_id, partial(
                _datasets().create_in_dashboard, user, dashboard_id, request,
                operation_id=operation_id, expected_revision=revision))
        except NovaException as exc:
            if exc.status_code == 409 and attempt == 1 and "SCHEMA_STALE" not in exc.message:
                continue
            raise
    raise Refusal("The dashboard keeps changing while this is saved. Try again shortly.")


async def _existing_reference(user: dict[str, Any], dataset_id: UUID) -> DatasetReference:
    revision, _ = await _dataset(user, dataset_id, None)
    return DatasetReference(dataset_id=dataset_id, revision=revision)


class CreateDashboardTool(_Change):
    name = "native_dashboard_create"
    args_model = CreateArgs
    description = (
        "Create a new Dataviz dashboard with one empty page, in the personal workspace unless "
        "a workspace_id from native_dashboard_list is given. Optionally attach an existing "
        "dataset (dataset_id) or a new dataset from a table (table: database, table, optional "
        "columns). Needs the user's approval. Returns the new dashboard_id and revision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 256},
            "workspace_id": {"type": "string", "maxLength": 128},
            "dataset_id": _UUID,
            "dataset_title": {"type": "string", "maxLength": 256},
            "table": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "maxLength": 128},
                    "table": {"type": "string", "maxLength": 128},
                    "kind": {"type": "string", "enum": ["table", "view"]},
                    "columns": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
                },
                "required": ["database", "table"],
                "additionalProperties": False,
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        title = str(invocation.arguments.get("title") or "Untitled")[:120]
        table = invocation.arguments.get("table")
        source = (f" with data from {table.get('table')}" if isinstance(table, dict)
                  else " with an existing dataset" if invocation.arguments.get("dataset_id")
                  else "")
        return f"Create the dashboard '{title}'{source}"

    async def act(self, args: CreateArgs, invocation, context, user) -> ToolOutcome:
        base = _base(invocation)
        identity = uuid5(base, "dashboard")
        references: tuple[DatasetReference, ...] = ()
        if args.dataset_id is not None:
            references = (await _existing_reference(user, args.dataset_id),)
        definition = DashboardDefinition(
            dashboard_id=identity, title=args.title,
            pages=(Page(page_id=uuid5(base, "page:1"), title="Page 1"),),
            datasets=references,
        )
        operation_id = uuid5(base, "create")
        receipt = await _settle(user, operation_id, lambda: _documents().create_visual(
            user, args.workspace_id, definition, operation_id=operation_id))
        if args.table is not None:
            receipt = await _attach_table(user, identity, base, args)
        change = _changed(identity, receipt, created=True)
        return _outcome(f"Created dashboard '{args.title}' (version {change['revision']})",
                        {"title": args.title, "page_id": str(uuid5(base, "page:1"))}, change)


class AttachDatasetTool(_Change):
    name = "native_dashboard_attach_dataset"
    args_model = AttachArgs
    description = (
        "Attach a dataset to a Dataviz dashboard (the open one when dashboard_id is left out) "
        "so its charts can use it: an existing dataset_id from the same workspace, or a new "
        "dataset from a table (table: database, table, optional columns). Needs the user's "
        "approval. Returns the saved revision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "dashboard_id": _UUID,
            "dataset_id": _UUID,
            "dataset_title": {"type": "string", "maxLength": 256},
            "table": CreateDashboardTool.parameters["properties"]["table"],
        },
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        table = invocation.arguments.get("table")
        if isinstance(table, dict):
            return f"Add data from {str(table.get('table'))[:80]} to the dashboard"
        return "Add an existing dataset to the dashboard"

    async def act(self, args: AttachArgs, invocation, context, user) -> ToolOutcome:
        identity = _dashboard_id(args.dashboard_id, context)
        base = _base(invocation)
        if args.table is not None:
            receipt = await _attach_table(user, identity, base, args)
            change = _changed(identity, receipt)
            return _outcome(f"Attached data from {args.table.table} "
                            f"(version {change['revision']})",
                            {"dataset_id": str(uuid5(base, "dataset"))}, change)
        assert args.dataset_id is not None
        reference = await _existing_reference(user, args.dataset_id)

        def build(dashboard: DashboardDefinition) -> list[Any]:
            current = [item for item in dashboard.datasets
                       if item.dataset_id != reference.dataset_id]
            return [DatasetsReplace(datasets=(*current, reference))]

        receipt = await apply_changes(user, identity, base, build)
        change = _changed(identity, receipt)
        return _outcome(f"Attached the dataset (version {change['revision']})",
                        {"dataset_id": str(reference.dataset_id),
                         "dataset_revision": reference.revision}, change)


class PublishTool(_Change):
    name = "native_dashboard_publish"
    args_model = PublishArgs
    description = (
        "Publish a Dataviz dashboard's current draft so everyone who can view it sees this "
        "version. Only when the user asks to publish. Needs the user's approval. Refuses when "
        "the draft changed since it was read."
    )
    parameters = {
        "type": "object",
        "properties": {"dashboard_id": _UUID,
                       "release_notes": {"type": "string", "maxLength": 2000}},
        "additionalProperties": False,
    }

    def preview(self, invocation: ToolInvocation) -> str:
        return "Publish the dashboard's current draft so everyone who can view it sees it"

    async def act(self, args: PublishArgs, invocation, context, user) -> ToolOutcome:
        identity = _dashboard_id(args.dashboard_id, context)
        operation_id = uuid5(_base(invocation), "publish")
        prior = await _receipt(user, operation_id)
        if prior is None:
            revision, _ = await _draft(user, identity)
            request = PublicationRequest(operation_id=operation_id, expected_revision=revision,
                                         release_notes=args.release_notes)
            prior = await _settle(user, operation_id,
                                  lambda: _documents().publish(user, identity, request))
        change = _changed(identity, prior, published=True)
        return _outcome(f"Published the dashboard (version {change['revision']})",
                        {"published_revision": change["revision"]}, change)


TOOLS = (
    ListDashboardsTool(),
    ReadDashboardTool(),
    DescribeDatasetTool(),
    QueryDatasetTool(),
    AddChartsTool(),
    UpdateChartTool(),
    ArrangeTool(),
    FiltersTool(),
    CreateDashboardTool(),
    AttachDatasetTool(),
    PublishTool(),
)
TOOL_NAMES = tuple(tool.name for tool in TOOLS)
READ_TOOLS = ("native_dashboard_read", "native_dataset_describe", "native_dataset_query")

PROMPT = """
## Native Dataviz dashboards

Dataviz dashboards are built from datasets, charts and a 12-column page grid. Every change runs
as the user's active role and is saved as a new version.
- "This dashboard" is the dashboard on screen. Read it (native_dashboard_read) before changing
  it, and use its dataset and field names exactly.
- To check numbers, call native_dataset_query on the dashboard's dataset and quote them exactly.
- To add a chart, call native_dashboard_add_charts with the visual and fields the user asked
  for; to change one, native_chart_update with its widget_id; to move, resize or remove,
  native_dashboard_arrange; filters, native_dashboard_filters.
- If the data is not on the dashboard yet, attach a dataset first.
- Publish only when the user asks to.
- Say a dashboard changed only when a tool confirmed it, with the saved version number (call it
  a version, as the app does). If a tool
  refused, say why in plain words and change nothing else.
""".strip()


def register(registry: Any) -> None:
    for tool in TOOLS:
        if registry.get(tool.name) is None:
            registry.register(tool)


async def brief(user: dict[str, Any], opened: dict[str, Any]) -> tuple[str, bool]:
    """The native dashboard on screen, read as the caller, for the turn's prompt.

    Returns the text and whether the dashboard was read (the same facts
    ``native_dashboard_read`` returns, so that call is settled).
    """
    import json

    identity = _uuid(opened.get("dashboard_id"))
    if identity is None:
        return "No dashboard is open.", False
    try:
        summary = await dashboard_summary(
            user, identity, selected=_uuid(opened.get("selected_widget_id")),
        )
    except (NovaException, Refusal) as exc:
        reason = _explain(exc) if isinstance(exc, NovaException) else str(exc)
        return f"The dashboard on screen could not be read right now. {reason}", False
    notes = [f"dashboard_id: {identity}"]
    page = _uuid(opened.get("page_id"))
    if page is not None:
        notes.append(f"Page on screen: {page}")
    shown = opened.get("revision")
    if isinstance(shown, int) and shown != summary["revision"]:
        notes.append(
            f"The page shows revision {shown}; the dashboard is now at revision "
            f"{summary['revision']}. Mention that it changed since the page loaded."
        )
    if not summary["editable"]:
        notes.append("The active role can view this dashboard but not change it.")
    body = json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
    return "\n".join(notes) + "\n" + body[:24_000], True
