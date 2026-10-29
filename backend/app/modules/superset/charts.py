"""Typed translation from Nova chart descriptions to Superset charts.

Nova describes a chart in three ways: a viz-core worksheet (Show Me and the
worksheet editor), a sanitized Vega-Lite spec (Intelligent UI charts), or
nothing at all (a saved result). Each is reduced to one small ``ChartPlan``,
and only the plan is turned into Superset ``form_data`` and a
``query_context``. The model never writes ``form_data``: it can only name a
chart kind and result columns, which are checked against the dataset's real
columns before anything is saved.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChartKind = Literal[
    "table", "bar", "bar_stacked", "line", "area", "scatter", "pie", "big_number", "heatmap"
]
Aggregate = Literal["SUM", "AVG", "MIN", "MAX", "COUNT", "COUNT_DISTINCT"]

DEFAULT_ROW_LIMIT = 10_000

_VIZ_TYPES: dict[str, str] = {
    "table": "table",
    "bar": "echarts_timeseries_bar",
    "bar_stacked": "echarts_timeseries_bar",
    "line": "echarts_timeseries_line",
    "area": "echarts_area",
    "scatter": "echarts_timeseries_scatter",
    "pie": "pie",
    "big_number": "big_number_total",
    "heatmap": "heatmap_v2",
}
_AXIS_KINDS = frozenset({"bar", "bar_stacked", "line", "area", "scatter"})

#: Worksheet aggregations Superset expresses directly. Others (median, stdev,
#: variance, ``attr``, ``agg``) are drawn over the already-computed value.
_AGGREGATES: dict[str, Aggregate] = {
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "distinct_count": "COUNT_DISTINCT",
}
_TIME_GRAINS = {"year": "P1Y", "quarter": "P3M", "month": "P1M", "week": "P1W", "day": "P1D"}
_MARK_KINDS: dict[str, ChartKind] = {
    "bar": "bar",
    "line": "line",
    "area": "area",
    "circle": "scatter",
    "square": "scatter",
    "tick": "scatter",
    "point": "scatter",
    "pie": "pie",
    "arc": "pie",
    "text": "table",
}


class Measure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=256)
    aggregate: Aggregate = "SUM"

    def metric(self) -> dict[str, Any]:
        return {
            "expressionType": "SIMPLE",
            "column": {"column_name": self.column},
            "aggregate": self.aggregate,
            "label": f"{self.aggregate}({self.column})",
        }


class ChartPlan(BaseModel):
    """One Superset chart, in terms of the dataset's result columns."""

    model_config = ConfigDict(extra="forbid")

    kind: ChartKind = "table"
    x: str | None = Field(default=None, max_length=256)
    time_grain: str | None = None
    series: str | None = Field(default=None, max_length=256)
    measures: list[Measure] = Field(default_factory=list, max_length=8)
    #: Raw table columns, when the chart lists rows rather than aggregating.
    columns: list[str] = Field(default_factory=list, max_length=64)
    limit: int = Field(default=DEFAULT_ROW_LIMIT, ge=1, le=100_000)

    def referenced(self) -> set[str]:
        names = {measure.column for measure in self.measures} | set(self.columns)
        names |= {name for name in (self.x, self.series) if name}
        return names


def table_plan(columns: list[str]) -> ChartPlan:
    return ChartPlan(kind="table", columns=columns[:64])


def plan_from_worksheet(spec: dict[str, Any]) -> ChartPlan:
    """Reduce a viz-core worksheet to a plan (shelves, marks and color)."""
    from app.modules.viz.spec import WorksheetSpec

    sheet = WorksheetSpec.model_validate(spec)
    pills = [*sheet.columns, *sheet.rows]
    dimensions = [pill for pill in pills if not pill.is_measure]
    measures = [
        Measure(column=pill.field, aggregate=_AGGREGATES.get(pill.aggregation or "", "MAX"))
        for pill in pills
        if pill.is_measure
    ]
    color = sheet.marks.color
    series = color.field if color is not None and not color.is_measure else None
    kind = _MARK_KINDS.get(sheet.marks.type, "bar")
    limit = sheet.limit or DEFAULT_ROW_LIMIT
    if not measures:
        names = [pill.field for pill in dimensions] or [pill.field for pill in pills]
        return ChartPlan(kind="table", columns=list(dict.fromkeys(names)), limit=limit)
    if not dimensions:
        if kind == "table" or len(measures) > 1:
            return ChartPlan(kind="table", measures=measures, limit=limit)
        return ChartPlan(kind="big_number", measures=measures[:1])
    first = dimensions[0]
    if series is None and len(dimensions) > 1 and kind != "table":
        series = dimensions[1].field
    if kind == "table":
        return ChartPlan(
            kind="table",
            columns=list(dict.fromkeys(pill.field for pill in dimensions)),
            measures=measures,
            limit=limit,
        )
    if kind == "bar" and series is not None:
        kind = "bar_stacked"
    return ChartPlan(
        kind=kind,
        x=first.field,
        time_grain=_TIME_GRAINS.get(first.date_part or ""),
        series=series if series != first.field else None,
        measures=measures if kind != "pie" else measures[:1],
        limit=limit,
    )


def plan_from_vega(spec: dict[str, Any], columns: list[str] | None = None) -> ChartPlan:
    """Reduce a sanitized Vega-Lite spec (Intelligent UI charts) to a plan.

    With ``columns`` unknown, fields are taken as named and checked against the
    dataset later by :func:`to_superset`.
    """
    mark = spec.get("mark")
    mark_type = mark.get("type") if isinstance(mark, dict) else mark
    raw_encoding = spec.get("encoding")
    encoding: dict[str, Any] = raw_encoding if isinstance(raw_encoding, dict) else {}

    def field(channel: str) -> str | None:
        value = encoding.get(channel)
        name = value.get("field") if isinstance(value, dict) else None
        if not isinstance(name, str) or (columns is not None and name not in columns):
            return None
        return name

    kind = _MARK_KINDS.get(str(mark_type or ""), "table")
    if kind == "pie":
        category, value = field("color"), field("theta")
        if category and value:
            return ChartPlan(kind="pie", x=category, measures=[Measure(column=value)])
        return table_plan(columns or [])
    x, y = field("x"), field("y")
    if kind == "table" or not x or not y:
        return table_plan(columns or [])
    series = field("color")
    if kind == "bar" and series:
        kind = "bar_stacked"
    return ChartPlan(kind=kind, x=x, series=series, measures=[Measure(column=y)])


def to_superset(
    plan: ChartPlan, *, dataset_id: int, columns: list[str], temporal: set[str]
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Return ``(viz_type, form_data, query_context)`` for ``plan``.

    Raises ``ValueError`` when the plan names a column the dataset lacks.
    """
    missing = plan.referenced() - set(columns)
    if missing:
        raise ValueError(f"The chart uses columns the dataset lacks: {', '.join(sorted(missing))}")
    viz_type = _VIZ_TYPES[plan.kind]
    datasource = f"{dataset_id}__table"
    metrics = [measure.metric() for measure in plan.measures]
    form: dict[str, Any] = {"viz_type": viz_type, "datasource": datasource}
    query: dict[str, Any] = {
        "columns": [],
        "metrics": metrics,
        "orderby": [],
        "row_limit": plan.limit,
        "filters": [],
        "extras": {},
    }

    if plan.kind == "table":
        if metrics:
            groupby = plan.columns or [name for name in (plan.x, plan.series) if name]
            form |= {"query_mode": "aggregate", "groupby": groupby, "metrics": metrics}
            query |= {"columns": groupby, "orderby": [[metrics[0], False]]}
        else:
            raw = plan.columns or columns[:64]
            form |= {"query_mode": "raw", "all_columns": raw}
            query |= {"columns": raw}
        form["row_limit"] = plan.limit
    elif plan.kind == "big_number":
        form |= {"metric": metrics[0]}
        query |= {"row_limit": 1}
    elif plan.kind == "pie":
        assert plan.x is not None
        form |= {"groupby": [plan.x], "metric": metrics[0], "row_limit": plan.limit}
        query |= {"columns": [plan.x], "orderby": [[metrics[0], False]]}
    elif plan.kind == "heatmap":
        assert plan.x is not None and plan.series is not None
        form |= {"x_axis": plan.x, "groupby": [plan.series], "metric": metrics[0]}
        query |= {"columns": [plan.x, plan.series]}
    else:
        assert plan.kind in _AXIS_KINDS and plan.x is not None
        grain = plan.time_grain if plan.x in temporal else None
        axis: str | dict[str, Any] = plan.x
        if grain:
            axis = {
                "columnType": "BASE_AXIS",
                "expressionType": "SQL",
                "sqlExpression": plan.x,
                "label": plan.x,
                "timeGrain": grain,
            }
        series = [plan.series] if plan.series else []
        form |= {
            "x_axis": plan.x,
            "time_grain_sqla": grain,
            "metrics": metrics,
            "groupby": series,
            "row_limit": plan.limit,
            "x_axis_sort_asc": True,
            "show_legend": bool(series),
        }
        if plan.kind == "bar_stacked":
            form["stack"] = "Stack"
        query |= {"columns": [axis, *series]}

    query_context = {
        "datasource": {"id": dataset_id, "type": "table"},
        "force": False,
        "queries": [query],
        "form_data": form,
        "result_format": "json",
        "result_type": "full",
    }
    return viz_type, form, query_context
