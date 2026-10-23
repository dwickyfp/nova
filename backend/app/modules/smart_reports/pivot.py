"""Pivot presentation of engine aggregates; subtotals never sum returned cells."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from app.modules.agents.semantic.calculations import total_calculations
from app.modules.agents.semantic.planning import SemanticOrder


def alias(field: str) -> str:
    return field.replace(".", "__")


async def pivot_result(widget, plan, source, execute, state) -> dict:
    column_fields = tuple(widget.pivot_columns)
    row_fields = tuple(field for field in plan.dimensions if field not in column_fields)
    if not row_fields or not set(column_fields) <= set(plan.dimensions):
        raise ValueError("Pivot grouping is incompatible; restore the configured row dimensions")
    calculations = total_calculations(plan.calculations, plan.metrics)
    if len(calculations) != len(plan.calculations):
        raise ValueError("Pivot calculations must be defined over aggregate metrics")
    metrics = tuple(alias(field) for field in plan.metrics) + tuple(c["name"] for c in calculations)

    async def aggregate(dimensions):
        result = await execute(
            source,
            replace(
                plan,
                dimensions=dimensions,
                order_by=tuple(SemanticOrder(f, "asc") for f in dimensions),
                limit=1001,
                offset=0,
                transforms=(),
                calculations=total_calculations(plan.calculations, plan.metrics),
            ),
        )
        if result.get("truncated") or len(result["rows"]) > 1000:
            raise ValueError("Pivot exceeds 1,000 groups; narrow the report filters")
        return result, [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]

    dimensions = [row_fields[:level] for level in range(len(row_fields), -1, -1)]
    aggregates = await asyncio.gather(*(aggregate(prefix + column_fields) for prefix in dimensions))
    totals = await asyncio.gather(*(aggregate(prefix) for prefix in dimensions))

    def key(values):
        return json.dumps(values, ensure_ascii=False, default=str)

    categories = {
        key([row[alias(f)] for f in column_fields]): [row[alias(f)] for f in column_fields]
        for row in aggregates[-1][1]
    }
    if len(categories) * len(metrics) > 100:
        raise ValueError("Pivot exceeds 100 columns; narrow the report filters")
    columns = [alias(f) for f in row_fields]
    labels = {}
    for index, values in enumerate(categories.values()):
        for metric in metrics:
            name = f"pivot_{index}_{metric}"
            columns.append(name)
            labels[name] = (
                " · ".join("(Blank)" if v is None else str(v) for v in values) + f" · {metric}"
            )
    for metric in metrics:
        columns.append(f"total_{metric}")
        labels[f"total_{metric}"] = f"Total · {metric}"

    output, kinds = [], []
    grand = {}
    for level, (prefix, (_, cells), (_, row_totals)) in enumerate(
        zip(dimensions, aggregates, totals, strict=True)
    ):
        by_row: dict[str, dict[str, dict]] = {}
        for cell in cells:
            row_key = key([cell[alias(f)] for f in prefix])
            col_key = key([cell[alias(f)] for f in column_fields])
            by_row.setdefault(row_key, {})[col_key] = cell
        for total in row_totals:
            values = [total[alias(f)] for f in prefix]
            cell_row = by_row.get(key(values), {})
            values += [None] * (len(row_fields) - len(prefix))
            for category in categories:
                values.extend(cell_row.get(category, {}).get(metric) for metric in metrics)
            values.extend(total.get(metric) for metric in metrics)
            if prefix:
                output.append(values)
                kinds.append("detail" if level == 0 else "subtotal")
            else:
                grand = dict(zip(columns, values, strict=True))
    start = state.page_index.get(widget.id, 0) * state.page_size
    return {
        "columns": columns,
        "column_labels": labels,
        "rows": output[start : start + state.page_size],
        "row_kinds": kinds[start : start + state.page_size],
        "totals": grand,
        "has_more": len(output) > start + state.page_size,
        "query_id": aggregates[0][0].get("query_id"),
        "warnings": aggregates[0][0].get("warnings", []),
    }
