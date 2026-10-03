"""Production-format starter catalog and independently reviewed teaching changes."""

from __future__ import annotations

import json

from app.modules.agents.semantic.ossie import parse_ossie
from tests.benchmark.business_intelligence.dataset import DATABASE, column_type
from tests.benchmark.business_intelligence.generate import tables


def expression(sql: str) -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def metric(name, dataset, sql, *, unit="count", description="", synonyms=(), **extra):
    time = {
        "orders": "orders.ordered_at",
        "order_items": "orders.ordered_at",
        "inventory_snapshots": "inventory_snapshots.observed_at",
        "campaign_spend": "campaign_spend.spent_at",
        "checkout_events": "checkout_events.occurred_at",
        "payment_attempts": "payment_attempts.attempted_at",
        "payment_failures": "payment_failures.occurred_at",
        "support_tickets": "support_tickets.created_at",
        "service_metrics": "service_metrics.observed_at",
        "campaign_exposures": "campaign_exposures.assigned_at",
    }
    return {
        "name": name,
        "base_dataset": dataset,
        "expression": expression(sql),
        "datatype": "Decimal",
        "unit": unit,
        "description": description,
        "synonyms": list(synonyms),
        "additivity": "additive",
        "default_time_dimension": time[dataset],
        **extra,
    }


def starter_definition(*, database: str = DATABASE) -> dict:
    from tests.benchmark.business_intelligence.outcome_training import TRAINING_DATABASE

    if database not in {DATABASE, TRAINING_DATABASE}:
        raise ValueError("Use an isolated benchmark or outcome-training database")
    datasets = []
    for name, rows in tables("small"):
        sample = next(rows)
        fields = []
        for field, value in sample.items():
            sql_type = column_type(field, value)
            is_time = sql_type == "DATETIME"
            dimension = is_time or isinstance(value, str) or field.endswith("_id")
            fields.append(
                {
                    "name": field,
                    "expression": expression(field),
                    "datatype": "DateTime"
                    if is_time
                    else "Decimal"
                    if "DECIMAL" in sql_type
                    else "Integer"
                    if sql_type == "BIGINT"
                    else "String",
                    **({"dimension": {"is_time": is_time}} if dimension else {}),
                }
            )
        datasets.append(
            {
                "name": name,
                "source": f"{database}.{name}",
                "primary_key": [next(iter(sample))],
                "fields": fields,
            }
        )
    links = [
        ("orders", "customers", "customer_id"),
        ("orders", "products", "product_id"),
        ("order_items", "orders", "order_id"),
        ("order_items", "products", "product_id"),
        ("product_cost_history", "products", "product_id"),
        ("refunds", "orders", "order_id"),
        ("inventory_snapshots", "products", "product_id"),
        ("inventory_snapshots", "warehouses", "warehouse_id"),
        ("stock_movements", "products", "product_id"),
        ("stock_movements", "warehouses", "warehouse_id"),
        ("fulfillment_events", "orders", "order_id"),
        ("campaign_spend", "campaigns", "campaign_id"),
        ("campaign_exposures", "customers", "customer_id"),
        ("campaign_exposures", "campaigns", "campaign_id"),
        ("web_sessions", "customers", "customer_id"),
        ("checkout_events", "web_sessions", "session_id"),
        ("payment_attempts", "orders", "order_id"),
        ("payment_attempts", "services", "service_id"),
        ("payment_failures", "payment_attempts", "attempt_id"),
        ("support_tickets", "customers", "customer_id"),
        ("customer_health_events", "customers", "customer_id"),
        ("deployments", "services", "service_id"),
        ("service_metrics", "services", "service_id"),
        ("incidents", "services", "service_id"),
    ]
    definition = {
        "version": "0.1.1",
        "name": "nova_business_360",
        "description": (
            "Governed cross-domain observations. Currency is IDR; business time is Asia/Jakarta."
        ),
        "datasets": datasets,
        "relationships": [
            {
                "name": f"{source}_{target}",
                "from": source,
                "to": target,
                "from_columns": [column],
                "to_columns": [column],
                "cardinality": "many_to_one",
            }
            for source, target, column in links
        ],
        "metrics": [
            metric(
                "gross_order_value",
                "orders",
                "SUM(orders.gross_amount)",
                unit="currency",
                currency="IDR",
                description=(
                    "Recorded gross order value across every status; no business eligibility rule."
                ),
            ),
            metric("order_count", "orders", "COUNT(*)"),
            metric(
                "order_completeness",
                "orders",
                "MIN(orders.complete)",
                unit="ratio",
                additivity="non_additive",
            ),
            metric(
                "inventory_units",
                "inventory_snapshots",
                "SUM(inventory_snapshots.sellable_units)",
                unit="units",
            ),
            metric("inventory_samples", "inventory_snapshots", "COUNT(*)"),
            metric(
                "inventory_completeness",
                "inventory_snapshots",
                "MIN(inventory_snapshots.complete)",
                unit="ratio",
                additivity="non_additive",
            ),
            metric(
                "campaign_spend",
                "campaign_spend",
                "SUM(campaign_spend.spend)",
                unit="currency",
                currency="IDR",
            ),
            metric("spend_samples", "campaign_spend", "COUNT(*)"),
            metric("checkout_samples", "checkout_events", "COUNT(*)"),
            metric("converted_sessions", "checkout_events", "SUM(checkout_events.converted)"),
            metric("payment_attempts", "payment_attempts", "COUNT(*)"),
            metric("payment_successes", "payment_attempts", "SUM(payment_attempts.success)"),
            metric(
                "support_resolution_hours",
                "support_tickets",
                "AVG(support_tickets.resolution_hours)",
                unit="hours",
                additivity="non_additive",
            ),
            metric("ticket_count", "support_tickets", "COUNT(*)"),
            metric(
                "payment_latency",
                "service_metrics",
                "AVG(service_metrics.latency_ms)",
                unit="milliseconds",
                additivity="non_additive",
            ),
            metric("service_samples", "service_metrics", "COUNT(*)"),
            metric(
                "service_completeness",
                "service_metrics",
                "MIN(service_metrics.complete)",
                unit="ratio",
                additivity="non_additive",
            ),
            metric(
                "experiment_conversion",
                "campaign_exposures",
                "AVG(campaign_exposures.converted)",
                unit="ratio",
                additivity="non_additive",
            ),
        ],
    }
    return parse_ossie(json.dumps(definition)).as_dict()


def teaching_changes() -> list[dict]:
    """Learning corpus definitions; no holdout wording, answers, or evaluator SQL."""
    healthy = "orders.status IN ('completed','fulfilled')"
    metrics = [
        metric(
            "net_booked_revenue",
            "orders",
            f"SUM(CASE WHEN {healthy} THEN orders.gross_amount-orders.refund_amount ELSE 0 END)",
            unit="currency",
            currency="IDR",
            owner_domain="Finance",
            authority="canonical",
            synonyms=["net booked revenue", "revenue", "omzet", "pendapatan diakui"],
            description="Healthy orders less posted refunds observed before the frozen clock.",
        ),
        metric(
            "healthy_orders",
            "orders",
            f"SUM(CASE WHEN {healthy} THEN 1 ELSE 0 END)",
            synonyms=["healthy orders", "pesanan sehat"],
            owner_domain="Operations",
        ),
        metric(
            "qualified_checkout_conversion",
            "checkout_events",
            (
                "SUM(CASE WHEN checkout_events.shipping_selected=1 THEN "
                "checkout_events.converted ELSE 0 END) / "
                "NULLIF(SUM(checkout_events.shipping_selected),0)"
            ),
            unit="ratio",
            additivity="non_additive",
            owner_domain="Marketing",
            synonyms=["qualified checkout conversion", "konversi checkout berkualitas"],
        ),
        metric(
            "recognized_gross_profit",
            "order_items",
            (
                f"SUM(CASE WHEN {healthy} THEN "
                "order_items.net_amount-order_items.unit_cost*order_items.quantity ELSE 0 END)"
            ),
            unit="currency",
            currency="IDR",
            owner_domain="Finance",
            authority="canonical",
            synonyms=["recognized gross profit", "laba kotor"],
        ),
        metric(
            "active_customers",
            "orders",
            (
                f"COUNT(DISTINCT CASE WHEN {healthy} AND orders.ordered_at >= '2026-02-09' "
                "AND orders.ordered_at < '2026-03-26' THEN orders.customer_id ELSE NULL END)"
            ),
            owner_domain="Operations",
            additivity="non_additive",
            synonyms=["active customer", "pelanggan aktif"],
            description="At least one healthy order in the trailing 45 days before 2026-03-26.",
        ),
        metric(
            "red_zone_skus",
            "inventory_snapshots",
            (
                "COUNT(DISTINCT CASE WHEN inventory_snapshots.daily_demand >= 20 "
                "AND inventory_snapshots.sellable_units < "
                "2*inventory_snapshots.daily_demand THEN "
                "inventory_snapshots.product_id ELSE NULL END)"
            ),
            owner_domain="Operations",
            additivity="non_additive",
            synonyms=["red-zone stockout", "stok zona merah"],
        ),
        metric(
            "recoverable_payment_failures",
            "payment_failures",
            "SUM(CASE WHEN payment_failures.code='payment_timeout' THEN 1 ELSE 0 END)",
            owner_domain="Operations",
            synonyms=["recoverable payment failure", "kegagalan pembayaran dapat dipulihkan"],
        ),
    ]
    changes = [{"kind": "metric", "name": item["name"], "definition": item} for item in metrics]
    changes.extend(
        [
            {
                "kind": "filter",
                "name": "greater_jakarta",
                "definition": {
                    "dataset": "orders",
                    "expression": "orders.city='Jakarta'",
                    "synonyms": ["JKT Core", "ibu kota"],
                },
            },
            {
                "kind": "filter",
                "name": "healthy_order",
                "definition": {"dataset": "orders", "expression": healthy},
            },
        ]
    )
    return changes
