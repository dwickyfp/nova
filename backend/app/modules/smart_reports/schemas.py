"""Versioned report definitions, edits and exploration contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.database import configured_timezone
from app.modules.agents.semantic.calculations import Calculation

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\w.-]+$")]
FieldName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w.]+$")]
Scalar = str | int | float | bool | None
ChartKind = Literal[
    "kpi",
    "line",
    "area",
    "bar",
    "table",
    "stacked_bar",
    "grouped_bar",
    "combo",
    "scatter",
    "donut",
    "heatmap",
    "waterfall",
    "pivot",
    "map",
    "funnel",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SemanticSource(Contract):
    id: Identifier
    view_id: Identifier
    version: int = Field(ge=1)
    fingerprint: str = Field(min_length=1, max_length=128)


class Predicate(Contract):
    field: FieldName
    operator: Literal[
        "=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "IS NULL", "IS NOT NULL", "LIKE"
    ] = "="
    value: Scalar | list[Scalar] = None

    @model_validator(mode="after")
    def bounded(self) -> Predicate:
        values = self.value if isinstance(self.value, list) else [self.value]
        if len(values) > 100 or any(isinstance(v, str) and len(v) > 1024 for v in values):
            raise ValueError("Filter selections exceed the permitted size")
        if self.operator in {"IN", "NOT IN"} and not isinstance(self.value, list):
            raise ValueError("Set filters require a list")
        if self.operator not in {"IN", "NOT IN"} and isinstance(self.value, list):
            raise ValueError("Scalar filters cannot contain a list")
        return self


class FilterValue(Contract):
    kind: Literal["unset", "single", "multi", "range", "date_range"] = "unset"
    values: list[Scalar] = Field(default_factory=list, max_length=100)
    minimum: Scalar = None
    maximum: Scalar = None
    relative: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def shape(self) -> FilterValue:
        if any(
            isinstance(v, str) and len(v) > 1024 for v in [*self.values, self.minimum, self.maximum]
        ):
            raise ValueError("Filter value is too long")
        if self.kind == "single" and len(self.values) != 1:
            raise ValueError("A single selection requires exactly one value")
        if self.kind == "unset" and (
            self.values or self.minimum is not None or self.maximum is not None or self.relative
        ):
            raise ValueError("An unset filter cannot contain values")
        if self.kind in {"single", "multi"} and (
            self.minimum is not None or self.maximum is not None or self.relative
        ):
            raise ValueError("Selection filters cannot include range bounds")
        if self.kind in {"range", "date_range"}:
            if self.values:
                raise ValueError("Range filters cannot include categorical values")
            if self.relative and (
                self.kind != "date_range" or self.minimum is not None or self.maximum is not None
            ):
                raise ValueError("Relative date ranges cannot contain explicit bounds")
            if not self.relative and self.minimum is None and self.maximum is None:
                raise ValueError("Range filters need a bound")
            bounds: list[Decimal | None] = []
            for value in (self.minimum, self.maximum):
                if value is None:
                    bounds.append(None)
                elif self.kind == "range":
                    try:
                        number = Decimal(str(value))
                    except InvalidOperation as exc:
                        raise ValueError("Numeric ranges require decimal bounds") from exc
                    if not number.is_finite():
                        raise ValueError("Range bounds must be finite")
                    bounds.append(number)
                elif isinstance(value, str):
                    bounds.append(Decimal(date.fromisoformat(value).toordinal()))
                else:
                    raise ValueError("Date ranges require ISO date bounds")
            if bounds[0] is not None and bounds[1] is not None and bounds[0] > bounds[1]:
                raise ValueError("Range start must not exceed its end")
            if self.relative:
                from app.modules.agents.semantic.time_ranges import is_supported_time_range

                if not is_supported_time_range(self.relative):
                    raise ValueError("Unsupported relative date range")
        return self


class FilterDefinition(Contract):
    id: Identifier
    label: str = Field(min_length=1, max_length=256)
    source: Identifier
    field: FieldName
    kind: Literal["single", "multi", "range", "date_range", "text", "boolean"]
    default: FilterValue = Field(default_factory=FilterValue)
    required: bool = False
    allow_null: bool = False
    scope: Literal["report", "page", "section", "widgets"] = "report"
    targets: list[Identifier] = Field(default_factory=list, max_length=240)
    exclude_widgets: list[Identifier] = Field(default_factory=list, max_length=240)
    parents: list[Identifier] = Field(default_factory=list, max_length=20)
    mappings: dict[Identifier, FieldName] = Field(default_factory=dict, max_length=20)

    def validate_value(self, value: FilterValue) -> None:
        expected = {"boolean": "single", "text": "single"}.get(self.kind, self.kind)
        if value.kind not in {expected, "unset"}:
            raise ValueError("Filter value does not match its type")
        if not self.allow_null and None in value.values:
            raise ValueError("This filter does not allow null")
        if self.kind == "boolean" and any(
            v is not None and type(v) is not bool for v in value.values
        ):
            raise ValueError("Boolean filters require boolean values")
        if self.kind == "text" and any(
            v is not None and not isinstance(v, str) for v in value.values
        ):
            raise ValueError("Text filters require text values")

    @model_validator(mode="after")
    def valid_default(self) -> FilterDefinition:
        self.validate_value(self.default)
        return self


class TimeSpec(Contract):
    dimension: FieldName
    grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    range: str | None = Field(default=None, max_length=128)
    compare: (
        Literal[
            "previous_period",
            "year_over_year",
            "quarter_over_quarter",
            "month_over_month",
            "week_over_week",
        ]
        | None
    ) = None


class Sort(Contract):
    field: FieldName
    direction: Literal["asc", "desc"] = "asc"


class Transform(Contract):
    metric: FieldName
    kind: Literal["share_of_total", "running_total", "rank"]


class QuerySpec(Contract):
    source: Identifier
    metrics: list[FieldName] = Field(default_factory=list, max_length=16)
    dimensions: list[FieldName] = Field(default_factory=list, max_length=12)
    filters: list[Predicate] = Field(default_factory=list, max_length=40)
    calculations: list[Calculation] = Field(default_factory=list, max_length=16)
    named_filters: list[Identifier] = Field(
        default_factory=list,
        max_length=40,
        description=(
            "Published semantic named-filter IDs only. Report filter IDs never belong here; "
            "report filters apply automatically."
        ),
    )
    time: TimeSpec | None = None
    order_by: list[Sort] = Field(default_factory=list, max_length=12)
    transforms: list[Transform] = Field(default_factory=list, max_length=16)
    limit: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def selected(self) -> QuerySpec:
        if not self.metrics and not self.dimensions:
            raise ValueError("Choose at least one metric or dimension")
        if len(set(self.metrics + self.dimensions)) != len(self.metrics + self.dimensions):
            raise ValueError("Query fields must be unique")
        return self


class Placement(Contract):
    col: int = Field(default=0, ge=0, le=11)
    row: int = Field(default=0, ge=0, le=10000)
    width: int = Field(default=6, ge=1, le=12)
    height: int = Field(default=2, ge=1, le=20)

    @model_validator(mode="after")
    def fits(self) -> Placement:
        if self.col + self.width > 12:
            raise ValueError("Widget must fit the 12-column grid")
        return self


class Encoding(Contract):
    x: FieldName | None = None
    y: list[FieldName] = Field(default_factory=list, max_length=16)
    series: FieldName | None = None
    value: FieldName | None = None
    latitude: FieldName | None = None
    longitude: FieldName | None = None


class ConditionalFormat(Contract):
    field: FieldName
    operator: Literal["<", "<=", "=", ">=", ">"]
    value: Decimal
    tone: Literal["positive", "negative", "attention"]


class MetricFormat(Contract):
    kind: Literal["number", "currency", "percent", "percentage_points"] = "number"
    precision: int = Field(default=2, ge=0, le=12)
    currency: str = Field(default="IDR", pattern=r"^[A-Z]{3}$")


class Widget(Contract):
    id: Identifier
    title: str = Field(min_length=1, max_length=256)
    kind: ChartKind = "table"
    query: QuerySpec | None = None
    encoding: Encoding = Field(default_factory=Encoding)
    format: MetricFormat = Field(default_factory=MetricFormat)
    placement: Placement = Field(default_factory=Placement)
    mapping_required: bool = False
    legacy_artifact_id: Identifier | None = None
    detail_fields: list[FieldName] = Field(default_factory=list, max_length=12)
    drill_dimensions: list[FieldName] = Field(default_factory=list, max_length=12)
    pivot_columns: list[FieldName] = Field(default_factory=list, max_length=4)
    conditional_formats: list[ConditionalFormat] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def runnable(self) -> Widget:
        if self.query is None and not self.mapping_required:
            raise ValueError("A widget requires a query or an explicit mapping request")
        if self.mapping_required and self.query is not None:
            raise ValueError("Unmapped widgets cannot execute a query")
        if self.query:
            dimensions = set(self.query.dimensions)
            if self.kind == "pivot" and (
                not self.pivot_columns
                or not set(self.pivot_columns) < dimensions
                or len(set(self.pivot_columns)) != len(self.pivot_columns)
                or self.query.time
                or self.query.transforms
            ):
                raise ValueError("Pivot requires row and column dimensions without time transforms")
            if self.kind == "heatmap" and (
                not self.encoding.x
                or not self.encoding.series
                or self.encoding.x == self.encoding.series
                or {self.encoding.x, self.encoding.series} != dimensions
                or len(self.query.metrics) != 1
            ):
                raise ValueError("Heatmap requires two distinct dimensions and one metric")
            if self.kind == "waterfall" and (
                len(dimensions) != 1 or len(self.query.metrics) != 1 or self.encoding.series
            ):
                raise ValueError(
                    "Waterfall requires one ordered dimension and one contribution metric"
                )
            if self.kind == "map" and (
                not self.encoding.latitude
                or not self.encoding.longitude
                or not {self.encoding.latitude, self.encoding.longitude} <= dimensions
            ):
                raise ValueError("Map requires governed latitude and longitude dimensions")
            if any(rule.field not in self.query.metrics for rule in self.conditional_formats):
                raise ValueError("Conditional formatting requires a selected metric")
        if (
            self.kind == "kpi"
            and self.query
            and (
                (len(self.query.metrics) != 1 and not self.encoding.value)
                or self.query.dimensions
                or self.query.time
                and self.query.time.grain
            )
        ):
            raise ValueError("A KPI requires one selected value without a grouped grain")
        return self


class Section(Contract):
    id: Identifier
    title: str = Field(default="", max_length=256)
    widgets: list[Widget] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def no_overlap(self) -> Section:
        cells: set[tuple[int, int]] = set()
        for widget in self.widgets:
            p = widget.placement
            occupied = {
                (x, y)
                for x in range(p.col, p.col + p.width)
                for y in range(p.row, p.row + p.height)
            }
            if cells & occupied:
                raise ValueError("Widgets cannot overlap")
            cells |= occupied
        return self


class Page(Contract):
    id: Identifier
    title: str = Field(min_length=1, max_length=256)
    sections: list[Section] = Field(min_length=1, max_length=24)


class Interaction(Contract):
    id: Identifier
    source_widget: Identifier
    field: FieldName
    targets: list[Identifier] = Field(min_length=1, max_length=240)
    mappings: dict[Identifier, FieldName] = Field(default_factory=dict, max_length=20)


class ReportCalendar(Contract):
    fiscal_start_month: int = Field(default=1, ge=1, le=12)
    week_start: int = Field(default=0, ge=0, le=6)


class ReportSpecV1(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)
    timezone: str = Field(default_factory=configured_timezone)
    calendar: ReportCalendar = Field(default_factory=ReportCalendar)
    locale: str = Field(default="id-ID", min_length=2, max_length=35)
    sources: list[SemanticSource] = Field(default_factory=list, max_length=20)
    filters: list[FilterDefinition] = Field(default_factory=list, max_length=30)
    pages: list[Page] = Field(min_length=1, max_length=10)
    interactions: list[Interaction] = Field(default_factory=list, max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unknown report timezone") from exc
        return value

    def widgets(self) -> dict[str, Widget]:
        return {w.id: w for p in self.pages for s in p.sections for w in s.widgets}

    @model_validator(mode="after")
    def references(self) -> ReportSpecV1:
        pages = [p.id for p in self.pages]
        sections = [s.id for p in self.pages for s in p.sections]
        widgets = [w.id for p in self.pages for s in p.sections for w in s.widgets]
        source_ids = [s.id for s in self.sources]
        filter_ids = [f.id for f in self.filters]
        ids = [*pages, *sections, *widgets, *filter_ids, *(i.id for i in self.interactions)]
        if len(ids) != len(set(ids)) or len(source_ids) != len(set(source_ids)):
            raise ValueError("Report element IDs must be unique")
        for page in self.pages:
            if sum(len(s.widgets) for s in page.sections) > 24:
                raise ValueError("A page supports at most 24 widgets")
        for w in self.widgets().values():
            if w.query and w.query.source not in source_ids:
                raise ValueError("Widget references an unknown source")
        for f in self.filters:
            if f.source not in source_ids or any(p not in filter_ids for p in f.parents):
                raise ValueError("Filter references an unknown source or parent")
            targets = {"report": [], "page": pages, "section": sections, "widgets": widgets}[
                f.scope
            ]
            if f.scope != "report" and (not f.targets or not set(f.targets) <= set(targets)):
                raise ValueError("Filter scope references an unknown target")
            if not set(f.exclude_widgets) <= set(widgets) or not set(f.mappings) <= set(source_ids):
                raise ValueError("Invalid filter mapping or exclusion")
        _acyclic({f.id: f.parents for f in self.filters})
        graph: dict[str, list[str]] = {}
        for i in self.interactions:
            if i.source_widget not in widgets or not set(i.targets) <= set(widgets):
                raise ValueError("Interaction references an unknown widget")
            graph.setdefault(i.source_widget, []).extend(i.targets)
            if not set(i.mappings) <= set(source_ids):
                raise ValueError("Unknown cross-filter source mapping")
        for widget_id in graph:
            if len({i.field for i in self.interactions if i.source_widget == widget_id}) > 1:
                raise ValueError("A cross-filter source must use one declared field")
        _acyclic(graph)
        if len(self.model_dump_json().encode()) > 512_000:
            raise ValueError("Report definition exceeds 512 KB")
        return self


def _acyclic(graph: dict[str, list[str]]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node: str) -> None:
        if node in active:
            raise ValueError("Filter or interaction dependency cycle")
        if node in visited:
            return
        active.add(node)
        for child in graph.get(node, []):
            visit(child)
        active.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


class Origin(Contract):
    agent_id: Identifier
    thread_id: Identifier
    message_id: Identifier | None = None
    content_id: Identifier | None = None


class ReportViewStateV1(Contract):
    schema_version: Literal[1] = 1
    session_id: Identifier
    report_id: Identifier
    draft_id: Identifier | None = None
    spec_revision: int = Field(ge=1)
    state_revision: int = Field(default=1, ge=1)
    active_page_id: Identifier
    filters: dict[Identifier, FilterValue] = Field(default_factory=dict, max_length=30)
    cross_filters: dict[Identifier, list[Scalar]] = Field(default_factory=dict, max_length=100)
    drill_paths: dict[Identifier, list[Predicate]] = Field(default_factory=dict, max_length=240)
    group_by: dict[Identifier, list[FieldName]] = Field(default_factory=dict, max_length=240)
    selected_widget_id: Identifier | None = None
    sort: dict[Identifier, list[Sort]] = Field(default_factory=dict, max_length=240)
    page_index: dict[Identifier, int] = Field(default_factory=dict, max_length=240)
    page_size: int = Field(default=50, ge=1, le=200)
    origin: Origin | None = None

    @model_validator(mode="after")
    def bounded_state(self) -> ReportViewStateV1:
        if any(len(path) > 12 for path in self.drill_paths.values()):
            raise ValueError("Drill paths support at most twelve levels")
        if len(self.model_dump_json().encode()) > 512 * 1024:
            raise ValueError("Report exploration state exceeds the size limit")
        return self


class ReportInteractionEvent(Contract):
    event_id: Identifier
    expected_state_revision: int = Field(ge=1)
    spec_revision: int = Field(ge=1)
    type: Literal[
        "FILTER_SET",
        "FILTER_RESET",
        "FILTER_RESET_ALL",
        "PAGE_SET",
        "WIDGET_FOCUS",
        "CROSS_FILTER_APPLY",
        "CROSS_FILTER_CLEAR",
        "DRILL_DOWN",
        "DRILL_UP",
        "DRILL_BY",
        "TABLE_SORT",
        "TABLE_PAGE",
    ]
    target: Identifier | None = None
    value: FilterValue | None = None
    field: FieldName | None = None
    values: list[Scalar] = Field(default_factory=list, max_length=100)
    sort: list[Sort] = Field(default_factory=list, max_length=12)
    page_index: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def bounded_event(self) -> ReportInteractionEvent:
        if any(isinstance(value, str) and len(value) > 1024 for value in self.values):
            raise ValueError("Selection value is too long")
        return self


class ReportPatch(Contract):
    op: Literal[
        "SET_REPORT_METADATA",
        "ADD_PAGE",
        "REMOVE_PAGE",
        "RENAME_PAGE",
        "MOVE_PAGE",
        "ADD_WIDGET",
        "REMOVE_WIDGET",
        "MOVE_WIDGET",
        "RESIZE_WIDGET",
        "SET_WIDGET_VISUAL",
        "SET_WIDGET_QUERY",
        "SET_WIDGET_FORMAT",
        "ADD_FILTER",
        "UPDATE_FILTER",
        "REMOVE_FILTER",
        "SET_INTERACTION",
        "REMOVE_INTERACTION",
        "SET_SOURCES",
        "RESTORE_DRAFT",
        "UNDO_DRAFT",
        "REDO_DRAFT",
    ]
    target: Identifier | None = None
    parent: Identifier | None = None
    value: dict[str, Any] = Field(default_factory=dict)


class PatchRequest(Contract):
    operation_id: Identifier
    expected_revision: int = Field(ge=1)
    operations: list[ReportPatch] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def bounded_patch(self) -> PatchRequest:
        if len(self.model_dump_json().encode()) > 512_000:
            raise ValueError("Report patch exceeds 512 KB")
        return self


class CreateReport(Contract):
    operation_id: Identifier
    spec: ReportSpecV1
    origin: Origin | None = None


class ViewCreate(Contract):
    revision: int | None = Field(default=None, ge=1)
    draft_id: Identifier | None = None
    origin: Origin | None = None
    preset_id: Identifier | None = None


class RenderRequest(Contract):
    view_session_id: Identifier
    expected_state_revision: int = Field(ge=1)
    widget_ids: list[Identifier] = Field(default_factory=list, max_length=24)
    request_generation: int = Field(default=0, ge=0)


class WidgetResult(Contract):
    widget_id: str
    status: Literal[
        "success", "empty", "denied", "error", "timeout", "requires_mapping", "incompatible"
    ]
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    column_labels: dict[str, str] = Field(default_factory=dict)
    row_kinds: list[Literal["detail", "subtotal"]] = Field(default_factory=list)
    completeness: Literal["complete", "paged", "truncated", "error"] = "complete"
    has_more: bool = False
    not_applied: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    time_context: dict[str, Any] | None = None
    query_fingerprint: str | None = None
    query_id: str | None = None
    semantic_view_id: str | None = None
    semantic_version: int | None = None
    evaluated_at: datetime | None = None
    message: str | None = None


class ReportContextRef(Contract):
    report_id: Identifier
    view_session_id: Identifier
    state_revision: int = Field(ge=1)
    selected_widget_id: Identifier | None = None


class GrantRequest(Contract):
    operation_id: Identifier
    expected_revision: int = Field(default=0, ge=0)
    target_type: Literal["user", "role"]
    target_name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][\w.\-]*$")
    permission: Literal["viewer", "editor", "publisher"]
    revoked: bool = False
