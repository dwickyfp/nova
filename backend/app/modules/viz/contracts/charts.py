"""Chart encodings are declarative data, never renderer options or callbacks."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Contract, DecimalValue, FieldId, Name, NumberFormat, Revision
from .filters import FilterTree

Aggregation = Literal[
    "sum", "avg", "min", "max", "count", "count_distinct", "median",
    "stdev", "stdevp", "var", "varp", "attr", "agg",
]
Visual = Literal[
    "kpi", "bar", "line", "area", "combo", "pie", "scatter", "heatmap",
    "calendar_heatmap", "histogram", "funnel", "gauge", "table", "pivot",
    "treemap", "sankey", "waterfall", "boxplot", "map",
]


class Dimension(Contract):
    field_id: FieldId
    discrete: bool = True
    date_grain: Literal["day", "week", "month", "quarter", "year"] | None = None
    date_mode: Literal["part", "truncate"] = "truncate"


class Measure(Contract):
    field_id: FieldId
    aggregation: Aggregation
    format: NumberFormat = Field(default_factory=NumberFormat)
    axis: Literal["left", "right"] = "left"
    series: Literal["bar", "line", "area"] = "bar"


class CartesianEncoding(Contract):
    kind: Literal["cartesian"] = "cartesian"
    dimensions: tuple[Dimension, ...] = Field(min_length=1, max_length=8)
    measures: tuple[Measure, ...] = Field(min_length=1, max_length=8)
    color: Dimension | None = None
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stack: Literal["none", "stacked", "percent"] = "none"
    interpolation: Literal["linear", "smooth", "step"] = "linear"
    nulls: Literal["gap", "connect", "zero"] = "gap"


class SingleMeasureEncoding(Contract):
    kind: Literal["single_measure"] = "single_measure"
    measure: Measure
    comparison: Measure | None = None
    target: str | None = Field(default=None, pattern=r"^-?[0-9]+(\.[0-9]+)?$", max_length=78)


class CategoryEncoding(Contract):
    kind: Literal["category"] = "category"
    category: Dimension
    measure: Measure
    donut: bool = False
    max_categories: int = Field(default=12, strict=True, ge=2, le=100)
    overflow: Literal["other", "error"] = "error"


class ScatterEncoding(Contract):
    kind: Literal["scatter"] = "scatter"
    x: FieldId
    y: FieldId
    size: FieldId | None = None
    color: Dimension | None = None
    detail: tuple[FieldId, ...] = Field(default=(), max_length=8)


class HeatmapEncoding(Contract):
    kind: Literal["heatmap"] = "heatmap"
    x: Dimension
    y: Dimension | None = None
    value: Measure


class DistributionEncoding(Contract):
    kind: Literal["distribution"] = "distribution"
    field_id: FieldId
    group: Dimension | None = None
    bins: int = Field(default=20, strict=True, ge=2, le=200)


class TableEncoding(Contract):
    kind: Literal["table"] = "table"
    dimensions: tuple[Dimension, ...] = Field(default=(), max_length=32)
    measures: tuple[Measure, ...] = Field(default=(), max_length=32)
    pivot_columns: tuple[Dimension, ...] = Field(default=(), max_length=8)
    subtotals: bool = False
    grand_total: bool = False
    page_size: int = Field(default=100, strict=True, ge=10, le=500)

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.dimensions and not self.measures:
            raise ValueError("A table needs a dimension or measure")
        return self


class HierarchyEncoding(Contract):
    kind: Literal["hierarchy"] = "hierarchy"
    levels: tuple[Dimension, ...] = Field(min_length=1, max_length=8)
    value: Measure


class FlowEncoding(Contract):
    kind: Literal["flow"] = "flow"
    source: Dimension
    target: Dimension
    value: Measure


class MapEncoding(Contract):
    kind: Literal["map"] = "map"
    latitude: FieldId
    longitude: FieldId
    value: Measure | None = None
    group: Dimension | None = None


Encoding = Annotated[
    CartesianEncoding | SingleMeasureEncoding | CategoryEncoding | ScatterEncoding
    | HeatmapEncoding | DistributionEncoding | TableEncoding | HierarchyEncoding
    | FlowEncoding | MapEncoding,
    Field(discriminator="kind"),
]


class ChartStyle(Contract):
    show_title: bool = True
    show_legend: bool = True
    show_labels: bool = False
    show_axis_titles: bool = True
    palette: Literal["neutral", "categorical", "single_tone"] = "neutral"
    description: str = Field(default="", max_length=2000)


class ChartSort(Contract):
    field_id: FieldId
    direction: Literal["asc", "desc"] = "asc"
    aggregation: Aggregation | None = None


class AxisStyle(Contract):
    title: str | None = Field(default=None, max_length=256)
    scale: Literal["linear", "log"] = "linear"
    minimum: DecimalValue | None = None
    maximum: DecimalValue | None = None
    include_zero: bool = True

    @model_validator(mode="after")
    def domain(self) -> Self:
        low = Decimal(self.minimum.value) if self.minimum is not None else None
        high = Decimal(self.maximum.value) if self.maximum is not None else None
        if low is not None and high is not None and low >= high:
            raise ValueError("Axis minimum must be below its maximum")
        if self.scale == "log" and (
            self.include_zero or any(bound is not None and bound <= 0 for bound in (low, high))
        ):
            raise ValueError("Logarithmic axes require positive bounds and exclude zero")
        return self


class TooltipStyle(Contract):
    visible: bool = True
    fields: tuple[FieldId, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Tooltip fields must be unique")
        return self


class ReferenceLine(Contract):
    kind: Literal["line"] = "line"
    annotation_id: UUID
    measure: Measure
    value: DecimalValue
    label: str = Field(default="", max_length=256)
    tone: Literal["neutral", "info", "warning", "destructive"] = "neutral"


class ReferenceBand(Contract):
    kind: Literal["band"] = "band"
    annotation_id: UUID
    measure: Measure
    minimum: DecimalValue
    maximum: DecimalValue
    label: str = Field(default="", max_length=256)
    tone: Literal["neutral", "info", "warning", "destructive"] = "neutral"

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if Decimal(self.minimum.value) >= Decimal(self.maximum.value):
            raise ValueError("Reference band minimum must be below its maximum")
        return self


Annotation = Annotated[ReferenceLine | ReferenceBand, Field(discriminator="kind")]


class TopN(Contract):
    dimension: Dimension
    measure: Measure
    count: int = Field(default=10, strict=True, ge=1, le=1000)
    direction: Literal["top", "bottom"] = "top"
    ties: Literal["include", "stable_first"] = "stable_first"
    remainder: Literal["omit", "other"] = "omit"
    other_label: Name = "Other"


class TableCalculation(Contract):
    calculation_id: UUID
    name: Name
    kind: Literal[
        "running_total", "percent_of_total", "difference", "percent_difference",
        "rank", "dense_rank", "moving_average",
    ]
    measure: Measure
    addressing: tuple[FieldId, ...] = Field(min_length=1, max_length=8)
    partition_by: tuple[FieldId, ...] = Field(default=(), max_length=8)
    order: tuple[ChartSort, ...] = Field(min_length=1, max_length=8)
    window_size: int | None = Field(default=None, strict=True, ge=1, le=1000)
    nulls: Literal["preserve", "zero"] = "preserve"

    @model_validator(mode="after")
    def window(self) -> Self:
        addressing, partition = set(self.addressing), set(self.partition_by)
        if (len(addressing) != len(self.addressing) or len(partition) != len(self.partition_by)
                or addressing & partition):
            raise ValueError("Calculation addressing and partitions must be unique and disjoint")
        order = [item.field_id for item in self.order]
        if len(set(order)) != len(order) or set(order) != addressing:
            raise ValueError("Calculation order must cover every addressing field exactly once")
        if any(item.aggregation is not None for item in self.order):
            raise ValueError("Calculation ordering uses dimension keys, not measure values")
        if (self.kind == "moving_average") != (self.window_size is not None):
            raise ValueError("A window size is required only for moving averages")
        return self


class ChartDefinition(Contract):
    schema_version: Literal[2] = 2
    chart_id: UUID
    title: Name
    dataset_id: UUID
    dataset_revision: Revision
    visual: Visual
    encoding: Encoding
    filters: FilterTree | None = None
    style: ChartStyle = Field(default_factory=ChartStyle)
    sort: tuple[ChartSort, ...] = Field(default=(), max_length=8)
    row_limit: int = Field(default=1000, strict=True, ge=1, le=10_000)
    result_overflow: Literal["error", "truncate_with_warning"] = "error"
    left_axis: AxisStyle = Field(default_factory=AxisStyle)
    right_axis: AxisStyle = Field(default_factory=AxisStyle)
    tooltip: TooltipStyle = Field(default_factory=TooltipStyle)
    annotations: tuple[Annotation, ...] = Field(default=(), max_length=32)
    top_n: TopN | None = None
    table_calculations: tuple[TableCalculation, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def visual_encoding(self) -> Self:
        expected = {
            "kpi": "single_measure", "gauge": "single_measure",
            "bar": "cartesian", "line": "cartesian", "area": "cartesian",
            "combo": "cartesian", "waterfall": "cartesian",
            "pie": "category", "funnel": "category", "scatter": "scatter",
            "heatmap": "heatmap", "calendar_heatmap": "heatmap",
            "histogram": "distribution", "boxplot": "distribution",
            "table": "table", "pivot": "table", "treemap": "hierarchy",
            "sankey": "flow", "map": "map",
        }
        if self.encoding.kind != expected[self.visual]:
            raise ValueError("Encoding is incompatible with the visualization")
        if (self.visual == "heatmap" and isinstance(self.encoding, HeatmapEncoding)
                and self.encoding.y is None):
            raise ValueError("A heatmap requires two dimensions")
        if (self.visual == "pivot" and isinstance(self.encoding, TableEncoding)
                and (not self.encoding.pivot_columns or not self.encoding.measures)):
            raise ValueError("A pivot requires column dimensions and measures")
        if (self.visual == "table" and isinstance(self.encoding, TableEncoding)
                and self.encoding.pivot_columns):
            raise ValueError("Pivot columns require the pivot visualization")
        if len({item.annotation_id for item in self.annotations}) != len(self.annotations):
            raise ValueError("Annotation IDs must be unique")
        if (len({item.calculation_id for item in self.table_calculations})
                != len(self.table_calculations)
                or len({item.name for item in self.table_calculations})
                != len(self.table_calculations)):
            raise ValueError("Table calculation IDs and names must be unique")
        return self
