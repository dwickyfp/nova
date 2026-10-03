"""Evaluator-only SQL, authored independently of the semantic compiler."""

from __future__ import annotations

DATABASE = "NOVA_INTELLIGENCE_BENCH"


def recognized_revenue(
    start: str,
    end: str,
    *,
    city: str | None = None,
    as_of: str = "2026-03-26",
) -> tuple[str, list]:
    return (
        f"SELECT SUM(o.gross_amount - COALESCE(r.refunds,0)) AS net_booked_revenue "
        f"FROM {DATABASE}.orders o LEFT JOIN (SELECT order_id,SUM(amount) refunds "
        f"FROM {DATABASE}.refunds WHERE status='posted' AND posted_at < %s "
        "GROUP BY order_id) r ON r.order_id=o.order_id "
        "WHERE o.status IN ('completed','fulfilled') AND o.ordered_at >= %s "
        "AND o.ordered_at < %s" + (" AND o.city=%s" if city else ""),
        [as_of, start, end, *([city] if city else [])],
    )


def profit_by_day(start: str, end: str) -> tuple[str, list]:
    return (
        "SELECT o.ordered_at,SUM(i.net_amount-i.unit_cost*i.quantity) AS gross_profit "
        f"FROM {DATABASE}.orders o JOIN {DATABASE}.order_items i ON i.order_id=o.order_id "
        "WHERE o.status IN ('completed','fulfilled') AND o.ordered_at>=%s "
        "AND o.ordered_at<%s GROUP BY o.ordered_at ORDER BY o.ordered_at",
        [start, end],
    )


def qualified_conversion(start: str, end: str, city: str | None = None) -> tuple[str, list]:
    return (
        "SELECT SUM(converted)*1.0/COUNT(*) AS qualified_conversion "
        f"FROM {DATABASE}.checkout_events WHERE shipping_selected=1 AND occurred_at>=%s "
        "AND occurred_at<%s" + (" AND city=%s" if city else ""),
        [start, end, *([city] if city else [])],
    )


def randomized_uplift() -> tuple[str, list]:
    return (
        "SELECT treatment,COUNT(*) AS sample_count,AVG(converted) AS conversion "
        f"FROM {DATABASE}.campaign_exposures GROUP BY treatment ORDER BY treatment",
        [],
    )


def city_contributions(start: str, end: str) -> tuple[str, list]:
    return (
        "SELECT city,SUM(CASE WHEN status IN ('completed','fulfilled') "
        "THEN gross_amount-refund_amount ELSE 0 END) AS recognized_revenue "
        f"FROM {DATABASE}.orders WHERE ordered_at>=%s AND ordered_at<%s "
        "GROUP BY city ORDER BY city",
        [start, end],
    )


def metric_case(parameters: dict) -> tuple[str, list]:
    metric = parameters["metric"]
    aggregate, source, timestamp, city, condition, args = "", "", "", "", "", []
    if metric in {"net_booked_revenue", "healthy_orders", "recognized_gross_profit"}:
        timestamp, city = "o.ordered_at", "o.city"
        condition = "o.status IN ('completed','fulfilled') AND "
        source = f"{DATABASE}.orders o"
        if metric == "net_booked_revenue":
            source += (
                f" LEFT JOIN (SELECT order_id,SUM(amount) AS posted FROM {DATABASE}.refunds "
                "WHERE status='posted' AND posted_at<%s GROUP BY order_id) r "
                "ON r.order_id=o.order_id"
            )
            args.append("2026-03-26")
            aggregate = "SUM(o.gross_amount-COALESCE(r.posted,0))"
        elif metric == "healthy_orders":
            aggregate = "COUNT(*)"
        else:
            source += f" JOIN {DATABASE}.order_items i ON i.order_id=o.order_id"
            aggregate = "SUM(i.net_amount-i.unit_cost*i.quantity)"
    elif metric == "qualified_checkout_conversion":
        timestamp, city, source = "occurred_at", "city", f"{DATABASE}.checkout_events"
        condition = "shipping_selected=1 AND "
        aggregate = "SUM(converted)*1.0/COUNT(*)"
    else:
        raise ValueError("No independent evaluator query for this metric")
    daily = bool(parameters["daily"])
    projection = f"CAST({timestamp} AS DATE)," if daily else ""
    sql = (
        f"SELECT {projection}{aggregate} FROM {source} WHERE {condition}"
        f"{timestamp}>=%s AND {timestamp}<%s AND {city}=%s"
    )
    if daily:
        sql += f" GROUP BY CAST({timestamp} AS DATE) ORDER BY CAST({timestamp} AS DATE)"
    return sql, [*args, parameters["start"], parameters["end"], parameters["city"]]
