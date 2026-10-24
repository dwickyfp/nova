"""The presentation model the Studio renderer consumes.

The view is rendered from a model built after the query returns:
:func:`build` takes a worksheet spec and a
result (raw rows from the conversation, or the rows the SQL compiler already
grouped) and produces everything the renderer needs — classified columns,
aggregated marks, legends, axis domains and totals.

The aggregation is idempotent: when the input rows already carry the
aggregated labels the spec asks for (the SQL pushdown path), grouping is
skipped, so one function serves both execution paths — in-core compute for
chat results, StarRocks pushdown for artifact refresh.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.modules.viz.spec import FieldRef, Filter, WorksheetSpec

MAX_RENDER_ROWS = 500


class PresModelError(ValueError):
    pass


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _group_key(row: dict[str, Any], fields: list[str]) -> tuple[Any, ...]:
    return tuple(_hashable(row.get(field)) for field in fields)


def _hashable(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return str(value)
    return value


def _aggregate(kind: str, values: list[Any]) -> Any:
    if kind == "count":
        return len([v for v in values if v is not None])
    if kind == "distinct_count":
        return len({_hashable(v) for v in values if v is not None})
    if kind == "attr":
        present = {_hashable(v) for v in values if v is not None}
        return next(iter(present)) if len(present) == 1 else None
    if kind == "agg":
        raise PresModelError("A calculated measure is computed by the engine, not in memory.")
    numbers = [n for n in (_numeric(v) for v in values) if n is not None]
    if not numbers:
        return None
    if kind == "sum":
        return sum(numbers)
    if kind == "avg":
        return sum(numbers) / len(numbers)
    if kind == "min":
        return min(numbers)
    if kind == "max":
        return max(numbers)
    if kind == "median":
        return statistics.median(numbers)
    if kind in {"stdev", "var"}:
        if len(numbers) < 2:
            return None
        return statistics.stdev(numbers) if kind == "stdev" else statistics.variance(numbers)
    if kind == "stdevp":
        return statistics.pstdev(numbers)
    if kind == "varp":
        return statistics.pvariance(numbers)
    raise PresModelError(f"Unsupported aggregation: {kind}")


def _filter_keeps(item: Filter, value: Any) -> bool:
    if item.kind == "quantitative":
        number = _numeric(value)
        if number is None:
            # A NULL row never satisfies a range filter.
            return False
        if item.min is not None:
            bound = _numeric(item.min)
            if bound is not None and number < bound:
                return False
        if item.max is not None:
            bound = _numeric(item.max)
            if bound is not None and number > bound:
                return False
        return True
    if item.kind == "categorical":
        cell = None if value is None else str(value)
        members = set(item.values)
        included = bool(item.null_included) if cell is None else cell in members
        return (not included) if item.exclude else included
    if value is None:
        return False
    point = _as_date(value)
    if point is None:
        return False
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    today = _date.today()
    if item.range == "last_n_days":
        return point >= today - _timedelta(days=item.n)
    if item.range == "last_n_months":
        month_index = today.year * 12 + (today.month - 1) - (item.n - 1)
        return point >= _date(month_index // 12, month_index % 12 + 1, 1)
    if item.range == "last_n_years":
        return point >= today.replace(month=1, day=1, year=today.year - (item.n - 1))
    if item.range == "month_to_date":
        return point >= today.replace(day=1)
    return point >= today.replace(month=1, day=1)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        head = value.strip()[:10].replace("/", "-")
        try:
            return datetime.strptime(head, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _input_rows(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    return [
        {column: row[index] if index < len(row) else None for index, column in enumerate(columns)}
        for row in rows
    ]


def _date_level(value: Any, part: str, discrete: bool) -> Any:
    """A date dimension at one level, as the engine's DATEPART/DATETRUNC would."""
    point = _as_date(value)
    if point is None:
        return None
    quarter = (point.month - 1) // 3 + 1
    if discrete:
        if part == "year":
            return point.year
        if part == "quarter":
            return quarter
        if part == "month":
            return point.month
        if part == "week":
            return int(point.strftime("%U"))
        return point.day
    if part == "year":
        start = point.replace(month=1, day=1)
    elif part == "quarter":
        start = point.replace(month=3 * (quarter - 1) + 1, day=1)
    elif part == "month":
        start = point.replace(day=1)
    elif part == "week":
        start = point - timedelta(days=point.weekday())
    else:
        start = point
    return start.isoformat()


def _derive_dimensions(records: list[dict[str, Any]], dimensions: list[FieldRef]) -> None:
    """Add each date-level dimension's value under its label."""
    for pill in dimensions:
        if pill.date_part is None:
            continue
        for row in records:
            row[pill.label] = _date_level(row.get(pill.field), pill.date_part, pill.discrete)


def _measure_output_columns(spec: WorksheetSpec) -> list[FieldRef]:
    measures = spec.measures() or [
        FieldRef(field="*", field_type="quantitative", aggregation="count")
    ]
    return measures


def _already_aggregated(spec: WorksheetSpec, columns: list[str], measures: list[FieldRef]) -> bool:
    """True when the input rows come from the SQL compiler: every measure
    label is already a column and every dimension is a column."""
    names = set(columns)
    dimensions = [d.label for d in spec.dimensions()]
    return all(m.label in names for m in measures) and (
        not dimensions or all(d in names for d in dimensions)
    )


def build(
    spec: WorksheetSpec,
    columns: list[str],
    rows: list[list[Any]],
    *,
    preserve_order: bool = False,
) -> dict[str, Any]:
    """Compute the presentation model for one worksheet.

    ``columns``/``rows`` are the source result: either the raw verified table
    a chat turn produced (aggregation happens here) or the compiler's grouped
    output (passed through). Filters, table calculations, sort and limit are
    applied in either case so interactive filtering behaves identically.
    ``preserve_order`` keeps the input order (table calculations computed by
    the engine ran along it).
    """
    records = _input_rows(columns, rows)
    measures = _measure_output_columns(spec)
    dimensions = spec.dimensions()
    dim_fields = [d.label for d in dimensions]

    for item in spec.filters:
        if item.field not in columns:
            continue
        records = [row for row in records if _filter_keeps(item, row.get(item.field))]

    aggregated_input = _already_aggregated(spec, columns, measures)
    if not aggregated_input:
        if any(m.aggregation == "agg" for m in measures):
            raise PresModelError("A calculated measure is computed by the engine, not in memory.")
        _derive_dimensions(records, dimensions)
    # A scatter with no dimension draws one mark per source row (an
    # unaggregated scatter); every other view aggregates its measures.
    row_level = not dimensions and spec.marks.type in {"circle", "square"}

    if aggregated_input:
        grouped: list[dict[str, Any]] = records
    elif row_level:
        grouped = [
            {measure.label: row.get(measure.field) for measure in measures} for row in records
        ]
    else:
        buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in records:
            buckets.setdefault(_group_key(row, dim_fields), []).append(row)
        grouped = []
        for key, bucket in buckets.items():
            combined = dict(zip(dim_fields, key, strict=True))
            for measure in measures:
                if measure.aggregation == "count" and measure.field == "*":
                    combined[measure.label] = len(bucket)
                else:
                    combined[measure.label] = _aggregate(
                        measure.aggregation or "sum",
                        [row.get(measure.field) for row in bucket],
                    )
            grouped.append(combined)

    if not preserve_order:
        grouped = _order_rows(spec, grouped, dimensions, measures)
    # Table calculations run over the view's display order (down the
    # table), so they are computed after sorting.
    grouped = _apply_table_calcs(spec, grouped, dimensions, measures)

    limit = spec.limit or MAX_RENDER_ROWS
    grouped = grouped[:limit]

    output_columns: list[FieldRef] = [*dimensions, *measures]
    for calc in spec.table_calcs:
        calc_source = next(
            (m for m in measures if m.field == calc.field or m.label == calc.field), None
        )
        if calc_source is not None:
            output_columns.append(
                FieldRef(
                    field=f"{calc.kind}({calc_source.label})",
                    field_type="quantitative",
                    discrete=False,
                )
            )

    output_rows = [
        [row.get(pill.label) for pill in output_columns]
        for row in grouped
    ]

    model: dict[str, Any] = {
        "version": 1,
        "title": spec.title,
        "mark": spec.marks.type,
        "columns": [_column_view(pill) for pill in output_columns],
        "rows": output_rows,
        "row_count": len(output_rows),
        "axes": _axes(spec, output_columns, output_rows),
        "legend": _legend(spec, output_columns, output_rows),
        "totals": _totals(output_columns, output_rows),
        "truncated": len(records) > 0 and len(output_rows) >= MAX_RENDER_ROWS,
    }
    return model


def _column_view(pill: FieldRef) -> dict[str, Any]:
    is_measure = pill.is_measure or (pill.field_type == "quantitative" and not pill.discrete)
    return {
        "name": pill.label,
        "field": pill.field,
        "field_type": pill.field_type,
        "role": "measure" if is_measure else "dimension",
        "aggregation": pill.aggregation,
        "discrete": pill.discrete,
        "label": pill.label,
        "date_part": pill.date_part,
    }


def _axes(spec: WorksheetSpec, columns: list[FieldRef], rows: list[list[Any]]) -> dict[str, Any]:
    dimensions = spec.dimensions()
    measures = spec.measures()
    x_view = None
    y_view = None
    if dimensions:
        first = dimensions[0]
        x_view = {
            "field": first.label,
            "label": first.label,
            "field_type": first.field_type,
            "discrete": first.discrete,
        }
    elif measures:
        x_view = {
            "field": measures[0].label,
            "label": measures[0].label,
            "field_type": "quantitative",
            "discrete": False,
        }
    if measures:
        y_view = {
            "field": measures[0].label,
            "label": measures[0].label,
            "field_type": "quantitative",
            "discrete": False,
        }
    elif len(dimensions) > 1:
        second = dimensions[1]
        y_view = {
            "field": second.label,
            "label": second.label,
            "field_type": second.field_type,
            "discrete": second.discrete,
        }
    return {"x": x_view, "y": y_view}


def _legend(spec: WorksheetSpec, columns: list[FieldRef], rows: list[list[Any]]) -> dict[str, Any]:
    color = spec.marks.color
    if color is None:
        return {"color": None}
    if color.is_measure:
        values: list[Any] = []
        for row in rows:
            index = next((i for i, pill in enumerate(columns) if pill.label == color.label), None)
            if index is not None and index < len(row):
                values.append(_numeric(row[index]))
        numeric = [v for v in values if v is not None]
        return {
            "color": {
                "field": color.label,
                "kind": "quantitative",
                "domain": [min(numeric), max(numeric)] if numeric else [0, 0],
            }
        }
    domain: list[Any] = []
    seen: set[Any] = set()
    for row in rows:
        index = next((i for i, pill in enumerate(columns) if pill.label == color.label), None)
        if index is not None and index < len(row) and row[index] not in seen:
            seen.add(row[index])
            domain.append(row[index])
    return {"color": {"field": color.label, "kind": "categorical", "domain": domain}}


#: Aggregations whose total over the marks is meaningful in memory.
_ADDITIVE_TOTALS = frozenset({"sum", "avg", "min", "max", "count"})


def _totals(columns: list[FieldRef], rows: list[list[Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for index, pill in enumerate(columns):
        if not pill.is_measure or pill.aggregation not in _ADDITIVE_TOTALS:
            continue
        values = [row[index] for row in rows if index < len(row)]
        total = _aggregate(pill.aggregation or "sum", values) if values else None
        if pill.aggregation in {"count"}:
            total = len(values)
        totals[pill.label] = total
    return totals


def _apply_table_calcs(
    spec: WorksheetSpec,
    grouped: list[dict[str, Any]],
    dimensions: list[FieldRef],
    measures: list[FieldRef],
) -> list[dict[str, Any]]:
    if not spec.table_calcs:
        return grouped
    rows = list(grouped)
    for calc in spec.table_calcs:
        measure = next(
            (m for m in measures if m.field == calc.field or m.label == calc.field), None
        )
        if measure is None:
            continue
        source = measure.label
        target = f"{calc.kind}({source})"
        if rows and target in rows[0]:
            # The engine already computed it along the view's order.
            continue
        values = [_numeric(row.get(source)) for row in rows]
        if calc.kind == "running_total":
            running = 0.0
            for row, value in zip(rows, values, strict=False):
                running += value or 0.0
                row[target] = running
        elif calc.kind == "percent_of_total":
            grand = sum(v or 0.0 for v in values)
            for row, value in zip(rows, values, strict=False):
                row[target] = (100.0 * (value or 0.0) / grand) if grand else 0.0
        elif calc.kind in {"difference", "percent_difference"}:
            previous: float | None = None
            for row, value in zip(rows, values, strict=False):
                current = value or 0.0
                if previous is None:
                    row[target] = current if calc.kind == "difference" else 0.0
                elif calc.kind == "difference":
                    row[target] = current - previous
                else:
                    row[target] = 100.0 * (current - previous) / previous if previous else 0.0
                previous = current if value is not None else previous
        elif calc.kind == "rank":
            snapshot = list(values)

            def rank_key(index: int, ordered: list = snapshot) -> float:
                value = ordered[index]
                return value if value is not None else float("-inf")

            order = sorted(range(len(rows)), key=rank_key, reverse=True)
            for position, index in enumerate(order, start=1):
                rows[index][target] = position
    return rows


def _order_rows(
    spec: WorksheetSpec,
    grouped: list[dict[str, Any]],
    dimensions: list[FieldRef],
    measures: list[FieldRef],
) -> list[dict[str, Any]]:
    rows = list(grouped)
    if spec.sort:
        for sort in reversed(spec.sort):
            field = sort.field
            dimension = next((d for d in dimensions if field in {d.label, d.field}), None)
            if dimension is not None:
                key_field = dimension.label
            elif any(m.label == field for m in measures):
                key_field = field
            else:
                measure = next(
                    (m for m in measures if m.field == field), measures[0] if measures else None
                )
                key_field = measure.label if measure else field
            rows.sort(
                key=lambda row: _sort_key(row.get(key_field)),
                reverse=sort.direction == "desc",
            )
        return rows
    if not dimensions or not measures:
        return rows
    # Default reading order, matching Nova's chart taste: bars by measure
    # (largest first), time and ordinal dimensions in natural order.
    first = dimensions[0]
    if first.field_type in {"temporal", "ordinal"}:
        rows.sort(key=lambda row: _sort_key(row.get(first.label)))
    elif spec.marks.type in {"bar", "pie", "text"} and not spec.marks.color:
        measure_label = measures[0].label
        rows.sort(key=lambda row: _sort_key(row.get(measure_label)), reverse=True)
    else:
        # Otherwise dimensions read in their natural (alphabetical) order, so
        # the engine's grouping order never reshuffles the axis between loads.
        labels = [d.label for d in dimensions]
        rows.sort(key=lambda row: tuple(_sort_key(row.get(label)) for label in labels))
    return rows


def _sort_key(value: Any) -> Any:
    number = _numeric(value)
    if number is not None:
        return (1, number, "")
    point = _as_date(value)
    if point is not None:
        return (1, point.toordinal(), "")
    return (0, 0.0, str(value) if value is not None else "")
