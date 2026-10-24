"""Compile a worksheet specification into one StarRocks SELECT.

This is the layer Tableau gives ``tabquery``: the semantic query built from
the shelves and marks card becomes SQL for the engine. StarRocks — not Hyper —
is that engine here, so:

* aggregation happens in SQL (``SUM``/``COUNT``/``COUNT DISTINCT``/...),
* filters are pushed into the source (``WHERE`` before ``GROUP BY``),
* table calculations become window functions over the grouped rows,
* the source (a read-only statement or a table) is wrapped as a CTE, the
  equivalent of Tableau's virtual table over the connection.

Every identifier is backtick-quoted and validated; literal values are
validated or escaped, because field names and filter members are user data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.common.sql_guard import split_sql_statements
from app.modules.viz.spec import (
    Aggregation,
    FieldRef,
    Filter,
    RelativeDateFilter,
    TableCalc,
    WorksheetSpec,
)

SOURCE_CTE = "nova_source"

_AGG_SQL: dict[Aggregation, str] = {
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "distinct_count": "COUNT",
}

_DATE_LIKE = re.compile(r"^\d{4}-\d{2}(-\d{2})([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?$|^\d{8}$")
_NUMBER_LIKE = re.compile(r"^-?\d+(\.\d+)?$")


class CompileError(ValueError):
    """The worksheet cannot be compiled safely."""


def quote_ident(name: str) -> str:
    """Backtick-quote a StarRocks identifier, rejecting anything unquoteable."""
    if not name or "\x00" in name:
        raise CompileError(f"Field name cannot be used in SQL: {name!r}")
    return f"`{name.replace('`', '``')}`"


def quote_literal(value: str) -> str:
    """Single-quote a string literal, doubling embedded quotes."""
    if "\x00" in value:
        raise CompileError("Filter values cannot contain null bytes.")
    return "'" + value.replace("'", "''") + "'"


def _literal(value: float | str) -> str:
    if isinstance(value, bool):
        raise CompileError("A boolean is not a quantitative filter bound.")
    if isinstance(value, (int, float)):
        return str(value)
    text = value.strip()
    if _NUMBER_LIKE.match(text):
        return text
    if _DATE_LIKE.match(text):
        return quote_literal(text)
    raise CompileError(f"Filter bound must be a number or date: {value!r}")


def validate_fields(spec: WorksheetSpec, known_columns: list[str]) -> None:
    """Reject any pill whose field is not a column of the source result."""
    known = set(known_columns)
    for pill in spec.all_fields():
        if pill.field not in known:
            raise CompileError(f"Field {pill.field!r} is not a column of this data source.")
    for item in spec.filters:
        if item.field not in known:
            raise CompileError(f"Filter field {item.field!r} is not a column of this data source.")
    for sort in spec.sort:
        if sort.field not in known and sort.field not in _measure_labels(spec):
            raise CompileError(
                f"Sort field {sort.field!r} is not a column or measure of this view."
            )
    for calc in spec.table_calcs:
        if calc.field not in known and calc.field not in _measure_labels(spec):
            raise CompileError(
                f"Table calculation field {calc.field!r} is not a measure of this view."
            )


def _measure_labels(spec: WorksheetSpec) -> set[str]:
    return {measure.label for measure in spec.measures()}


@dataclass
class CompiledQuery:
    """The compiled statement plus the output field order it promises."""

    sql: str
    output_fields: list[FieldRef] = field(default_factory=list)


def source_expression(spec: WorksheetSpec) -> str:
    """The source as a subquery expression, validated to be one statement."""
    source = spec.source
    if source.kind == "table":
        return f"SELECT * FROM {quote_ident(source.database)}.{quote_ident(source.table)}"
    statements = split_sql_statements(source.sql)
    if len(statements) != 1:
        raise CompileError("A viz source must be exactly one SQL statement.")
    return f"({source.sql.strip()})"


def source_probe_sql(spec: WorksheetSpec) -> str:
    """A one-row probe used to learn the source's columns and types."""
    return f"SELECT * FROM {source_expression(spec)} AS {SOURCE_CTE} LIMIT 1"


def _aggregated(pill: FieldRef) -> str:
    assert pill.aggregation is not None
    fn = _AGG_SQL[pill.aggregation]
    column = quote_ident(pill.field)
    if pill.aggregation == "distinct_count":
        return f"COUNT(DISTINCT {column})"
    if pill.aggregation == "count":
        return f"COUNT({column})"
    return f"{fn}({column})"


def _relative_date_clause(item: RelativeDateFilter) -> str:
    column = quote_ident(item.field)
    window = item.range
    if window == "last_n_days":
        return f"{column} >= DATE_SUB(CURRENT_DATE, INTERVAL {int(item.n)} DAY)"
    if window == "last_n_months":
        return f"{column} >= DATE_SUB(CURRENT_DATE, INTERVAL {int(item.n)} MONTH)"
    if window == "last_n_years":
        return f"{column} >= DATE_SUB(CURRENT_DATE, INTERVAL {int(item.n)} YEAR)"
    if window == "month_to_date":
        return f"{column} >= DATE_TRUNC(CURRENT_DATE, 'month')"
    return f"{column} >= DATE_TRUNC(CURRENT_DATE, 'year')"


def _filter_clauses(filters: list[Filter]) -> list[str]:
    clauses: list[str] = []
    for item in filters:
        column = quote_ident(item.field)
        if item.kind == "quantitative":
            if item.min is not None:
                clauses.append(f"{column} >= {_literal(item.min)}")
            if item.max is not None:
                clauses.append(f"{column} <= {_literal(item.max)}")
            if item.min is None and item.max is None:
                raise CompileError("A quantitative filter needs a min or a max.")
        elif item.kind == "categorical":
            if not item.values:
                clauses.append(f"{column} IS NULL" if not item.exclude else "1 = 1")
                continue
            members = ", ".join(quote_literal(value) for value in item.values)
            null_part = " OR " + f"{column} IS NULL" if item.null_included else ""
            if item.exclude:
                clauses.append(f"NOT ({column} IN ({members}){null_part})")
            else:
                clauses.append(f"({column} IN ({members}){null_part or ''})")
        else:
            clauses.append(_relative_date_clause(item))
    return clauses


def _calc_select(calc: TableCalc, measure_sql: str, partition: list[str]) -> str:
    partition_by = f"PARTITION BY {', '.join(partition)}" if partition else ""
    order_by = f"ORDER BY {', '.join(partition)}" if partition else ""
    if calc.kind == "running_total":
        over = " ".join(part for part in (partition_by, order_by) if part)
        return f"SUM({measure_sql}) OVER ({over})"
    if calc.kind == "percent_of_total":
        return f"100.0 * {measure_sql} / NULLIF(SUM({measure_sql}) OVER ({partition_by}), 0)"
    if calc.kind in {"difference", "percent_difference"}:
        over = " ".join(part for part in (partition_by, order_by) if part)
        lag = f"LAG({measure_sql}) OVER ({over})"
        if calc.kind == "difference":
            return f"COALESCE({measure_sql} - {lag}, {measure_sql})"
        return f"100.0 * COALESCE(({measure_sql} - {lag}) / NULLIF({lag}, 0), 0)"
    over = " ".join(part for part in (partition_by, f"ORDER BY {measure_sql} DESC") if part)
    return f"RANK() OVER ({over})"


def compile_worksheet(
    spec: WorksheetSpec, *, known_columns: list[str] | None = None
) -> CompiledQuery:
    """Compile the worksheet to one StarRocks SELECT.

    ``known_columns``, when given, pins every referenced field to a real source
    column; without it fields are still identifier-quoted, never raw.
    """
    if known_columns is not None:
        validate_fields(spec, known_columns)

    dimensions = spec.dimensions()
    measures = spec.measures()
    row_level = not dimensions and spec.marks.type in {"circle", "square"}
    if not measures and spec.marks.type not in {"bar", "text"}:
        # A count of rows is still renderable for bar/text views.
        measures = [FieldRef(field="*", field_type="quantitative", aggregation="count")]

    select_parts: list[str] = []
    output_fields: list[FieldRef] = []
    for pill in dimensions:
        select_parts.append(quote_ident(pill.field))
        output_fields.append(pill)
    for pill in measures:
        alias = pill.label
        expression = quote_ident(pill.field) if row_level else _aggregated(pill)
        select_parts.append(f"{expression} AS {quote_ident(alias)}")
        output_fields.append(pill)

    inner = f"SELECT {', '.join(select_parts)} FROM {source_expression(spec)} AS {SOURCE_CTE}"
    where = _filter_clauses(spec.filters)
    if where:
        inner += " WHERE " + " AND ".join(where)
    if dimensions and not row_level:
        inner += " GROUP BY " + ", ".join(quote_ident(p.field) for p in dimensions)

    order_parts: list[str] = []
    labels = {m.label for m in measures}
    dimension_fields = {d.field for d in dimensions}
    for sort in spec.sort:
        if sort.field in labels or sort.field in dimension_fields:
            target = quote_ident(sort.field)
        else:
            target = _aggregated(_measure_by_field(spec, sort.field))
        order_parts.append(f"{target} {'ASC' if sort.direction == 'asc' else 'DESC'}")
    if order_parts:
        inner += " ORDER BY " + ", ".join(order_parts)
    if spec.limit is not None:
        inner += f" LIMIT {int(spec.limit)}"

    sql = inner
    if spec.table_calcs:
        axis = [quote_ident(p.field) for p in dimensions[:-1]]
        outer_parts = [f"{SOURCE_CTE}.*"]
        for calc in spec.table_calcs:
            measure = _measure_by_field_or_label(spec, calc.field)
            measure_sql = quote_ident(measure.label)
            alias = f"{calc.kind}({measure.label})"
            outer_parts.append(f"{_calc_select(calc, measure_sql, axis)} AS {quote_ident(alias)}")
            output_fields.append(
                FieldRef(
                    field=alias,
                    field_type="quantitative",
                    aggregation=measure.aggregation,
                    discrete=False,
                )
            )
        sql = f"SELECT {', '.join(outer_parts)} FROM ({inner}) AS {SOURCE_CTE}"

    return CompiledQuery(sql=sql, output_fields=output_fields)


def _measure_by_field(spec: WorksheetSpec, field_name: str) -> FieldRef:
    for measure in spec.measures():
        if measure.field == field_name:
            return measure
    raise CompileError(f"Sort field {field_name!r} is not a measure of this view.")


def _measure_by_field_or_label(spec: WorksheetSpec, name: str) -> FieldRef:
    for measure in spec.measures():
        if measure.field == name or measure.label == name:
            return measure
    raise CompileError(f"Table calculation field {name!r} is not a measure of this view.")


def to_vega_lite(spec: WorksheetSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A sanitized Vega-Lite mirror of the worksheet, for replay paths that
    still speak Vega (persisted answers, exports)."""
    mark = spec.marks.type
    mark_map = {
        "bar": "bar",
        "line": "line",
        "area": "area",
        "circle": "circle",
        "square": "square",
        "tick": "tick",
        "pie": "arc",
        "text": "text",
    }
    measures = spec.measures() or [
        FieldRef(field="*", field_type="quantitative", aggregation="count")
    ]
    dimensions = spec.dimensions()

    def vega_type(pill: FieldRef) -> str:
        if pill.field_type == "temporal":
            return "temporal"
        if not pill.discrete or pill.is_measure:
            return "quantitative"
        return "nominal"

    def encoding_field(pill: FieldRef) -> dict[str, Any]:
        return {"field": pill.label if pill.is_measure else pill.field, "type": vega_type(pill)}

    encoding: dict[str, Any] = {}
    if mark == "pie":
        encoding["theta"] = encoding_field(measures[0])
        if dimensions:
            encoding["color"] = {"field": dimensions[0].field, "type": "nominal"}
    elif mark == "text":
        if dimensions:
            encoding["x"] = {"field": dimensions[0].field, "type": "nominal"}
            if len(dimensions) > 1:
                encoding["y"] = {"field": dimensions[1].field, "type": "nominal"}
        else:
            encoding["x"] = {"field": measures[0].label, "type": "quantitative"}
        encoding["text"] = encoding_field(measures[0])
    else:
        if dimensions:
            encoding["x"] = {
                "field": dimensions[0].field,
                "type": "temporal"
                if dimensions[0].field_type == "temporal"
                else ("quantitative" if not dimensions[0].discrete else "nominal"),
            }
            encoding["y"] = encoding_field(measures[0])
        elif len(measures) > 1:
            encoding["x"] = encoding_field(measures[0])
            encoding["y"] = encoding_field(measures[1])
        else:
            encoding["x"] = {"field": measures[0].label, "type": "quantitative"}
            encoding["y"] = {
                "field": measures[0].label,
                "type": "quantitative",
                "aggregate": "count",
            }
    if spec.marks.color is not None and mark != "pie":
        encoding["color"] = encoding_field(spec.marks.color)
    if spec.marks.size is not None and mark != "pie":
        encoding["size"] = encoding_field(spec.marks.size)
    spec_out: dict[str, Any] = {
        "$schema": "https://vega-lite.github.io/schema/vega-lite/v5.json",
        "mark": mark_map.get(mark, "bar"),
        "width": "container",
        "data": {"values": rows},
        "encoding": encoding,
    }
    if spec.title:
        spec_out["title"] = spec.title
    return spec_out
