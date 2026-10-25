"""Worksheet and dashboard specifications.

A view is authored by placing fields on shelves; the contract is expressed
here as typed JSON so the assistant, the Studio editor and persisted
artifacts all speak one language:

* a **worksheet** (``WorksheetSpec``) is a chart: column/row shelves of field
  pills, a marks card (mark type plus color/size/text/detail/angle encodings),
  filters, sort and top-N;
* a **dashboard** (``DashboardSpec``) is a zone layout over chart artifacts,
  with dashboard-level filters and filter/highlight actions.

Every field pill is discrete or continuous: discrete fields make headers,
continuous fields make axes.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

FieldType = Literal["nominal", "ordinal", "quantitative", "temporal"]

#: Pill aggregations. ``agg`` marks a pill over a calculated field that is
#: already an aggregate or table calculation (shown as ``AGG(name)``).
Aggregation = Literal[
    "sum",
    "avg",
    "median",
    "min",
    "max",
    "count",
    "distinct_count",
    "stdev",
    "stdevp",
    "var",
    "varp",
    "attr",
    "agg",
]

#: Date levels a date dimension pill can show. A discrete pill shows the
#: part (``MONTH`` = 1..12); a continuous pill truncates to it (a timeline).
DatePart = Literal["year", "quarter", "month", "week", "day"]

MarkType = Literal[
    "bar",
    "line",
    "area",
    "circle",
    "square",
    "tick",
    "pie",
    "text",
]

#: Marks that need at least one measure on an axis or encoding.
MEASURE_MARKS = frozenset({"pie", "text"})

TableCalcKind = Literal[
    "running_total",
    "percent_of_total",
    "difference",
    "percent_difference",
    "rank",
]

#: The longest field identifier accepted when no column list can validate it.
MAX_FIELD_LENGTH = 128

#: Most calculated fields one worksheet may define.
MAX_CALCULATIONS = 64


class FieldRef(BaseModel):
    """A field pill: a source column plus how it participates in the view.

    ``aggregation`` marks a measure pill (``SUM(Sales)``); its absence marks a
    dimension pill. Discrete pills produce headers, continuous pills produce
    axes.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    field_type: FieldType = "nominal"
    aggregation: Aggregation | None = None
    discrete: bool = True
    date_part: DatePart | None = None

    @property
    def is_measure(self) -> bool:
        return self.aggregation is not None

    @property
    def label(self) -> str:
        """The result column this pill produces (unique per pill meaning)."""
        if self.aggregation == "agg":
            return self.field
        if self.aggregation is not None:
            return f"{self.aggregation}({self.field})"
        if self.date_part is not None:
            suffix = "" if self.discrete else "_trunc"
            return f"{self.date_part}{suffix}({self.field})"
        return self.field

    def continuous(self) -> Self:
        clone = self.model_copy()
        clone.discrete = False
        return clone


class QuantitativeFilter(BaseModel):
    """A numeric or date range filter."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["quantitative"] = "quantitative"
    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    min: float | str | None = None
    max: float | str | None = None
    #: Set for a measure filter: the range applies to the aggregated value
    #: per mark (after aggregation) instead of to each row.
    aggregation: Aggregation | None = None
    #: A context filter applies before FIXED level-of-detail calculations.
    context: bool = False


class CategoricalFilter(BaseModel):
    """A member-list filter."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["categorical"] = "categorical"
    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    values: list[str] = Field(default_factory=list, max_length=1000)
    exclude: bool = False
    null_included: bool | None = None
    context: bool = False


class RelativeDateFilter(BaseModel):
    """A rolling or to-date window filter."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["relative_date"] = "relative_date"
    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    range: Literal[
        "last_n_days",
        "last_n_months",
        "last_n_years",
        "month_to_date",
        "year_to_date",
    ]
    n: int = Field(default=1, ge=1, le=1000)
    context: bool = False


Filter = Annotated[
    QuantitativeFilter | CategoricalFilter | RelativeDateFilter,
    Field(discriminator="kind"),
]

_FILTER_ADAPTER: Any = None


def parse_filter(raw: object) -> Filter:
    """Validate one filter payload (an annotated union, so not a model)."""
    global _FILTER_ADAPTER
    if _FILTER_ADAPTER is None:
        from pydantic import TypeAdapter

        _FILTER_ADAPTER = TypeAdapter(Filter)
    return _FILTER_ADAPTER.validate_python(raw)


class TableCalc(BaseModel):
    """A quick table calculation applied after aggregation."""

    model_config = ConfigDict(extra="forbid")

    kind: TableCalcKind
    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)


class CalculatedField(BaseModel):
    """A named formula (see :mod:`app.modules.viz.calc`).

    ``addressing`` sets the direction of table calculations in the formula:
    the view dimensions they run along. The remaining view dimensions
    partition (restart) the calculation. ``None`` runs along every
    dimension of the view (the whole table).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    formula: str = Field(min_length=1, max_length=8000)
    addressing: list[str] | None = Field(default=None, max_length=8)


class SortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    direction: Literal["asc", "desc"] = "asc"


class VizSourceSql(BaseModel):
    """The source is one read-only SQL statement — the artifact's provenance."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sql"] = "sql"
    sql: str = Field(min_length=1, max_length=200_000)


class VizSourceTable(BaseModel):
    """The source is a table the caller is authorized to read."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["table"] = "table"
    database: str = Field(min_length=1, max_length=128)
    table: str = Field(min_length=1, max_length=128)


VizSource = Annotated[VizSourceSql | VizSourceTable, Field(discriminator="kind")]


class MarksCard(BaseModel):
    """The marks card: the mark type and its field encodings."""

    model_config = ConfigDict(extra="forbid")

    type: MarkType = "bar"
    color: FieldRef | None = None
    size: FieldRef | None = None
    text: FieldRef | None = None
    detail: FieldRef | None = None
    angle: FieldRef | None = None


class WorksheetSpec(BaseModel):
    """A chart: one worksheet as typed JSON."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    title: str = Field(default="", max_length=256)
    source: VizSource
    columns: list[FieldRef] = Field(default_factory=list, max_length=8)
    rows: list[FieldRef] = Field(default_factory=list, max_length=8)
    marks: MarksCard = Field(default_factory=MarksCard)
    filters: list[Filter] = Field(default_factory=list, max_length=32)
    sort: list[SortSpec] = Field(default_factory=list, max_length=4)
    limit: int | None = Field(default=None, ge=1, le=10_000)
    table_calcs: list[TableCalc] = Field(default_factory=list, max_length=8)
    calculations: list[CalculatedField] = Field(default_factory=list, max_length=MAX_CALCULATIONS)
    #: Field-type hints from the producing result, keyed by column name.
    field_types: dict[str, FieldType] = Field(default_factory=dict)

    @model_validator(mode="after")
    def shelves_not_empty(self) -> Self:
        if not self.columns and not self.rows:
            raise ValueError("A worksheet needs at least one field on a shelf.")
        names = [calc.name for calc in self.calculations]
        if len(set(names)) != len(names):
            raise ValueError("Calculated field names must be unique.")
        return self

    def calculation_formulas(self) -> dict[str, str]:
        return {calc.name: calc.formula for calc in self.calculations}

    def calculation(self, name: str) -> CalculatedField | None:
        return next((calc for calc in self.calculations if calc.name == name), None)

    def all_fields(self) -> list[FieldRef]:
        """Every field pill the view reads: shelves, encodings, filters, sort."""
        pills: list[FieldRef] = [*self.columns, *self.rows]
        marks = self.marks
        for encoding in (marks.color, marks.size, marks.text, marks.detail, marks.angle):
            if encoding is not None:
                pills.append(encoding)
        return pills

    def dimensions(self) -> list[FieldRef]:
        """Dimension pills that group the view (its level of detail)."""
        seen: dict[str, FieldRef] = {}
        for pill in self.all_fields():
            if not pill.is_measure:
                seen.setdefault(pill.label, pill)
        return list(seen.values())

    def measures(self) -> list[FieldRef]:
        """Aggregated pills, deduplicated by their aggregated label."""
        seen: dict[str, FieldRef] = {}
        for pill in self.all_fields():
            if pill.is_measure:
                seen.setdefault(pill.label, pill)
        return list(seen.values())

    def with_extra_filters(self, extra: list[Filter]) -> WorksheetSpec:
        """A copy with dashboard/cross-filter filters merged in.

        A filter already present for the same field is replaced: the more
        specific interactive filter wins, which is how dashboard filter
        actions compose with worksheet filters.
        """
        merged = {f.field: f for f in self.filters}
        for item in extra:
            merged[item.field] = item
        return self.model_copy(update={"filters": list(merged.values())})


class ZoneRect(BaseModel):
    """Zone bounds on the dashboard grid: 24 columns, unbounded rows."""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, le=23)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=48)

    @model_validator(mode="after")
    def within_grid(self) -> Self:
        if self.x + self.w > 24:
            raise ValueError("A dashboard zone must fit inside the 24-column grid.")
        return self


class TextZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2000)
    style: Literal["title", "body"] = "body"


class FilterZone(BaseModel):
    """A dashboard quick filter: one field applied to sheets."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=MAX_FIELD_LENGTH)
    apply_to: list[str] = Field(default_factory=lambda: ["all"], max_length=48)


class Zone(BaseModel):
    """One dashboard zone: sheet, text, filter or spacer."""

    model_config = ConfigDict(extra="forbid")

    zone_id: str = Field(min_length=1, max_length=64)
    kind: Literal["sheet", "text", "filter", "spacer"]
    rect: ZoneRect
    artifact_id: str | None = Field(default=None, max_length=64)
    text: TextZone | None = None
    filter: FilterZone | None = None

    @model_validator(mode="after")
    def kind_payload(self) -> Self:
        if self.kind == "sheet" and not self.artifact_id:
            raise ValueError("A sheet zone needs the chart artifact it renders.")
        if self.kind != "sheet" and self.artifact_id:
            raise ValueError("Only sheet zones reference an artifact.")
        if self.kind == "text" and self.text is None:
            raise ValueError("A text zone needs its text payload.")
        if self.kind == "filter" and self.filter is None:
            raise ValueError("A filter zone needs its filter payload.")
        if self.kind in {"sheet", "spacer"} and (self.text or self.filter):
            raise ValueError("Sheet and spacer zones carry no text or filter payload.")
        return self


class DashboardAction(BaseModel):
    """A dashboard action.

    ``filter`` re-queries target sheets with the selected members; ``highlight``
    marks matching the selection without changing any data.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=64)
    kind: Literal["filter", "highlight"]
    source_zone: str = Field(min_length=1, max_length=64)
    target_zones: list[str] = Field(default_factory=lambda: ["all"], max_length=48)
    field: str | None = None
    run_on: Literal["select", "menu"] = "select"


class DashboardSize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "fixed"] = "auto"
    width: int | None = Field(default=None, ge=200, le=4096)
    height: int | None = Field(default=None, ge=200, le=4096)


class DashboardSpec(BaseModel):
    """A dashboard artifact: zones over chart artifacts plus shared filters."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    title: str = Field(default="", max_length=256)
    size: DashboardSize = Field(default_factory=DashboardSize)
    zones: list[Zone] = Field(default_factory=list, max_length=48)
    actions: list[DashboardAction] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def zones_valid(self) -> Self:
        seen_ids: set[str] = set()
        occupied: set[tuple[int, int]] = set()
        for zone in self.zones:
            if zone.zone_id in seen_ids:
                raise ValueError("Dashboard zone IDs must be unique.")
            seen_ids.add(zone.zone_id)
            rect = zone.rect
            cells = {
                (x, y)
                for x in range(rect.x, rect.x + rect.w)
                for y in range(rect.y, rect.y + rect.h)
            }
            if occupied & cells:
                raise ValueError("Dashboard zones cannot overlap.")
            occupied.update(cells)
        known = seen_ids
        for action in self.actions:
            if action.source_zone not in known:
                raise ValueError("A dashboard action names an unknown source zone.")
            for target in action.target_zones:
                if target != "all" and target not in known:
                    raise ValueError("A dashboard action names an unknown target zone.")
        return self

    def sheet_zones(self) -> list[Zone]:
        return [zone for zone in self.zones if zone.kind == "sheet"]


class ChartIntent(BaseModel):
    """The assistant↔viz bridge: a small, checkable chart request.

    The model (or a user's Show Me pick) names a chart type and fields; the
    ShowMe placement engine resolves it into a full worksheet.
    """

    model_config = ConfigDict(extra="forbid")

    chart_type: Literal[
        "auto",
        "text_table",
        "bar",
        "bar_stacked",
        "bar_side",
        "line",
        "area",
        "scatter",
        "pie",
        "bubble",
        "heatmap",
    ] = "auto"
    x: str | None = Field(default=None, max_length=MAX_FIELD_LENGTH)
    y: str | list[str] | None = None
    color: str | None = Field(default=None, max_length=MAX_FIELD_LENGTH)
    size: str | None = Field(default=None, max_length=MAX_FIELD_LENGTH)
    aggregation: Aggregation | None = None
