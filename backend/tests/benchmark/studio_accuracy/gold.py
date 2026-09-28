"""An independent oracle: gold SQL written from a case's expectation.

This module must not import Nova's semantic planner, compiler, or time-range
grammar. It re-derives calendar windows with plain ``datetime`` arithmetic and
writes SQL by hand against the physical ``NOVA_BENCH`` tables, so an execution
match is evidence that the compiler is right, not that it agrees with itself.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tests.benchmark.studio_accuracy.model import DATABASE


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _add_months(value: date, months: int) -> date:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    return date(year, month, 1) if value.day == 1 else date(year, month, value.day)


def _period_start(unit: str, today: date) -> date:
    if unit == "day":
        return today
    if unit == "week":
        return today - timedelta(days=today.weekday())
    if unit == "month":
        return _month_start(today)
    if unit == "quarter":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    return date(today.year, 1, 1)


def _step(start: date, unit: str, count: int) -> date:
    if unit == "day":
        return start + timedelta(days=count)
    if unit == "week":
        return start + timedelta(weeks=count)
    months = {"month": 1, "quarter": 3, "year": 12}[unit]
    return _add_months(start, months * count)


def window(value: str, today: date) -> tuple[date, date, str | None]:
    """``(start, end_exclusive, calendar_unit)`` for a canonical range string."""
    text = value.strip().lower()
    if match := re.fullmatch(r"(current|previous)_(day|week|month|quarter|year)", text):
        unit = match[2]
        start = _period_start(unit, today)
        if match[1] == "previous":
            start = _step(start, unit, -1)
        return start, _step(start, unit, 1), unit
    if match := re.fullmatch(r"last_(\d+)_(day|week|month|quarter|year)s", text):
        unit, count = match[2], int(match[1])
        end = _period_start(unit, today)
        return _step(end, unit, -count), end, unit if count == 1 else None
    if text in {"ytd", "qtd", "mtd"}:
        unit = {"ytd": "year", "qtd": "quarter", "mtd": "month"}[text]
        return _period_start(unit, today), today + timedelta(days=1), None
    if match := re.fullmatch(r"(\d{4})-q([1-4])", text):
        start = date(int(match[1]), 3 * int(match[2]) - 2, 1)
        return start, _add_months(start, 3), "quarter"
    if match := re.fullmatch(r"(\d{4})-(\d{2})", text):
        start = date(int(match[1]), int(match[2]), 1)
        return start, _add_months(start, 1), "month"
    if re.fullmatch(r"\d{4}", text):
        return date(int(text), 1, 1), date(int(text) + 1, 1, 1), "year"
    if match := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", text):
        start = date.fromisoformat(match[1])
        return start, date.fromisoformat(match[2]) + timedelta(days=1), None
    if match := re.fullmatch(r"since_(\d{4})", text):
        return date(int(match[1]), 1, 1), today + timedelta(days=1), None
    raise ValueError(f"The oracle does not know range {value!r}.")


def comparison_windows(
    value: str, compare: str, today: date
) -> tuple[date, date, date, date]:
    """Current and prior windows; a period in progress is compared up to today."""
    start, end, _unit = window(value, today)
    prior_start, prior_end = prior_window(value, compare, today)
    match = re.fullmatch(r"current_(week|month|quarter|year)", value.strip().lower())
    if match:
        # Same calendar day of the prior period ("QTD vs prior QTD").
        end = today + timedelta(days=1)
        months = {"year_over_year": 12, "quarter_over_quarter": 3, "month_over_month": 1}.get(
            compare, {"month": 1, "quarter": 3, "year": 12}.get(match[1])
        )
        if compare == "week_over_week" or (months is None and match[1] == "week"):
            prior_end = end - timedelta(weeks=1)
        else:
            prior_end = _shift_end(end, int(months or 0))
    return start, end, prior_start, prior_end


def prior_window(value: str, compare: str, today: date) -> tuple[date, date]:
    start, end, unit = window(value, today)
    if compare == "year_over_year":
        return _add_months(start, -12) if start.day == 1 else _shift_days_year(start), (
            _add_months(end, -12) if end.day == 1 else _shift_days_year(end)
        )
    if compare == "month_over_month":
        return _add_months(start, -1), _add_months(end, -1)
    if compare == "quarter_over_quarter":
        return _add_months(start, -3), _add_months(end, -3)
    if compare == "week_over_week":
        return start - timedelta(weeks=1), end - timedelta(weeks=1)
    # previous_period: the window's own length, in calendar units when it has one.
    text = value.strip().lower()
    if text in {"ytd", "qtd", "mtd"}:
        months = {"ytd": 12, "qtd": 3, "mtd": 1}[text]
        return _add_months(start, -months), _shift_end(end, months)
    if match := re.fullmatch(r"last_(\d+)_(day|week|month|quarter|year)s", text):
        count, step_unit = int(match[1]), match[2]
        return _step(start, step_unit, -count), start
    if unit is not None:
        return _step(start, unit, -1), start
    length = end - start
    return start - length, start


def _shift_days_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:  # 29 February
        return value.replace(year=value.year - 1, day=28)


def _shift_end(end: date, months: int) -> date:
    # ``end`` is "tomorrow" for to-date windows; shift the day, not the month start.
    year = end.year + (end.month - 1 - months) // 12
    month = (end.month - 1 - months) % 12 + 1
    import calendar

    return date(year, month, min(end.day, calendar.monthrange(year, month)[1]))


METRICS = {
    "total_revenue": ("SUM(o.total_amount)", "orders"),
    "order_count": ("COUNT(DISTINCT o.order_id)", "orders"),
    "avg_order_value": ("SUM(o.total_amount) / NULLIF(COUNT(DISTINCT o.order_id), 0)", "orders"),
    "customer_count": ("COUNT(DISTINCT o.customer_id)", "orders"),
    "units_sold": ("SUM(oi.quantity)", "items"),
    "product_revenue": ("SUM(oi.line_amount)", "items"),
    "marketing_spend_total": ("SUM(m.amount)", "marketing"),
}
DIMENSIONS = {
    "city": ("o.shipping_city", "orders"),
    "sales_channel": ("o.sales_channel", "orders"),
    "status": ("o.status", "orders"),
    "segment": ("c.segment", "customers"),
    "category": ("p.category", "products"),
    "marketing_channel": ("m.channel", "marketing"),
}
NAMED = {"completed_order": "o.status = 'completed'"}


#: The channel a marketing row and an order row share (a conformed dimension).
CONFORMED = {("marketing", "sales_channel"): "m.channel"}


def gold_sql(expect: dict[str, Any], today: date, *, database: str = DATABASE) -> str:
    metrics = list(expect.get("metrics") or [])
    facts = list(dict.fromkeys(METRICS[name][1] for name in metrics))
    if len(facts) > 1:
        return _drill_across(expect, facts, today, database)
    sql = _single(expect, facts[0], today, database)
    return _post(sql, expect)


def _drill_across(expect: dict[str, Any], facts: list[str], today: date, database: str) -> str:
    dimensions = list(expect.get("dimensions") or [])
    parts = []
    for index, fact in enumerate(facts):
        names = [name for name in expect["metrics"] if METRICS[name][1] == fact]
        parts.append((f"g{index}", names, _single(
            {**expect, "metrics": names, "limit": None}, fact, today, database,
            aliases=True,
        )))
    select = [
        "COALESCE(" + ", ".join(f"{alias}.k{position}" for alias, _n, _s in parts)
        + f") AS k{position}"
        for position, _name in enumerate(dimensions)
    ]
    select += [f"{alias}.{name}" for alias, names, _sql in parts for name in names]
    sql = "SELECT " + ", ".join(select) + f" FROM ({parts[0][2]}) {parts[0][0]}"
    for alias, _names, sub in parts[1:]:
        if dimensions:
            on = " AND ".join(
                f"{parts[0][0]}.k{position} = {alias}.k{position}"
                for position in range(len(dimensions))
            )
            sql += f" FULL OUTER JOIN ({sub}) {alias} ON {on}"
        else:
            sql += f" CROSS JOIN ({sub}) {alias}"
    return sql


def _post(sql: str, expect: dict[str, Any]) -> str:
    """Share, metric conditions and top-N per group, written as plain window SQL."""
    metric = expect["metrics"][0]
    transform = expect.get("transform")
    having = expect.get("having") or {}
    top = expect.get("top_n_per_group")
    if not (transform or having or top):
        return sql
    extra = []
    if transform == "share_of_total":
        extra.append(f"{metric} * 100.0 / SUM({metric}) OVER () AS share")
    if top:
        partition = ", ".join(f"k_{name}" for name in top["partition_by"])
        extra.append(f"ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY {metric} DESC) AS rn")
    inner = f"SELECT t.*{', ' if extra else ''}{', '.join(extra)} FROM ({sql}) t"
    conditions = [f"{name} {operator} {value}" for name, (operator, value) in having.items()]
    if top:
        conditions.append(f"rn <= {int(top['n'])}")
    keep = [f"k_{name}" for name in expect.get("dimensions") or []] + list(expect["metrics"])
    if transform == "share_of_total":
        keep.append("share")
    outer = f"SELECT {', '.join(keep)} FROM ({inner}) x"
    return outer + (" WHERE " + " AND ".join(conditions) if conditions else "")


def _single(
    expect: dict[str, Any], fact: str, today: date, database: str, *, aliases: bool = False,
) -> str:
    metrics = list(expect.get("metrics") or [])
    joins: list[str] = []
    if fact == "marketing":
        source, time_column = f"{database}.marketing_spend m", "m.spend_date"
    elif fact == "items":
        source = f"{database}.order_items oi"
        joins.append(f"JOIN {database}.orders o ON oi.order_id = o.order_id")
        time_column = "o.order_date"
    else:
        source, time_column = f"{database}.orders o", "o.order_date"
    dimensions = list(expect.get("dimensions") or [])
    filters: dict[str, Any] = dict(expect.get("filters") or {})
    needed = {
        DIMENSIONS[name][1] for name in [*dimensions, *filters] if (fact, name) not in CONFORMED
    }
    if "customers" in needed:
        joins.append(f"JOIN {database}.customers c ON o.customer_id = c.customer_id")
    if "products" in needed:
        joins.append(f"JOIN {database}.products p ON oi.product_id = p.product_id")

    select: list[str] = []
    group: list[str] = []
    where: list[str] = []
    range_value, compare, grain = expect.get("range"), expect.get("compare"), expect.get("grain")
    if range_value:
        start, end, _unit = window(range_value, today)
        if compare:
            start, end, prior_start, prior_end = comparison_windows(range_value, compare, today)
            label = (
                f"CASE WHEN {time_column} >= '{start}' THEN 'current_period' "
                "ELSE 'previous_period' END"
            )
            select.append(label)
            group.append(label)
            where.append(
                f"(({time_column} >= '{start}' AND {time_column} < '{end}') OR "
                f"({time_column} >= '{prior_start}' AND {time_column} < '{prior_end}'))"
            )
        else:
            where.append(f"{time_column} >= '{start}' AND {time_column} < '{end}'")
    if grain:
        bucket = f"DATE_TRUNC('{grain}', {time_column})"
        select.append(bucket)
        group.append(bucket)
    for position, name in enumerate(dimensions):
        column = CONFORMED.get((fact, name), DIMENSIONS[name][0])
        label = f"k{position}" if aliases else f"k_{name}"
        select.append(f"{column} AS {label}")
        group.append(column)
    for name, value in filters.items():
        column = CONFORMED.get((fact, name), DIMENSIONS[name][0])
        if isinstance(value, list):
            where.append(f"{column} IN (" + ", ".join(f"'{item}'" for item in value) + ")")
        else:
            where.append(f"{column} = '{value}'")
    for name in expect.get("named_filters") or []:
        where.append(NAMED[name])
    select.extend(f"{METRICS[name][0]} AS {name}" for name in metrics)
    sql = f"SELECT {', '.join(select)} FROM {source} " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group:
        sql += " GROUP BY " + ", ".join(group)
    if expect.get("limit"):
        sql += f" ORDER BY {metrics[0]} DESC LIMIT {int(expect['limit'])}"
    return sql


def normalize_rows(rows: list[Any], *, ordered: bool = False) -> list[tuple[str, ...]]:
    """Rows as comparable tuples: numbers to 2 places, dates as ISO, cells sorted.

    Cells are sorted within a row so that column order (and naming) does not
    matter; only the values do.
    """
    output = []
    for row in rows:
        cells = []
        for value in row:
            if value is None:
                cells.append("null")
            elif isinstance(value, datetime):
                cells.append(value.date().isoformat())
            elif isinstance(value, date):
                cells.append(value.isoformat())
            else:
                try:
                    # compute_metrics writes percentages as "20.4763%".
                    cells.append(str(Decimal(str(value).rstrip("%")).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )))
                except Exception:  # noqa: BLE001 - text cell
                    cells.append(str(value))
        output.append(tuple(sorted(cells)))
    return output if ordered else sorted(output)
