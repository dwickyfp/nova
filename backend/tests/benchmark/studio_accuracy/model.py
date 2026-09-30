"""The ``nova_bench`` Semantic View used by the Studio accuracy benchmark.

Built as an Ossie document (the same format a user publishes) and parsed with
Nova's own parser, so the benchmark exercises the production path. Synonyms
are English and Indonesian on purpose: the audit found Indonesian business
words ("penjualan", "omzet") unmatched.
"""

from __future__ import annotations

from typing import Any

import yaml

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie

DATABASE = "NOVA_BENCH"

CITIES = ("Jakarta", "Bandung", "Surabaya", "Medan", "Makassar")
CHANNELS = ("Website", "Mobile App", "Marketplace", "Store")
SEGMENTS = ("Retail", "SME", "Enterprise")
CATEGORIES = ("Electronics", "Fashion", "Grocery", "Beauty", "Home")
STATUSES = ("completed", "cancelled", "pending")


def _expr(text: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": text}]}


def _field(
    name: str,
    expression: str | None = None,
    *,
    dimension: bool = False,
    time: bool = False,
    datatype: str | None = None,
    synonyms: tuple[str, ...] = (),
    samples: tuple[str, ...] = (),
    description: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "expression": _expr(expression or name),
        "description": description or name.replace("_", " "),
    }
    if datatype:
        field["datatype"] = datatype
    if time:
        field["dimension"] = {"is_time": True}
        field["datatype"] = datatype or "Date"
    elif dimension:
        field["dimension"] = {"sample_values": list(samples)} if samples else {}
    if synonyms:
        field["ai_context"] = {"synonyms": list(synonyms)}
    return field


def _metric(
    name: str,
    expression: str,
    *,
    synonyms: tuple[str, ...],
    time: str = "order_date",
    datatype: str = "Decimal",
    description: str = "",
    filters: tuple[str, ...] = (),
) -> dict[str, Any]:
    metric: dict[str, Any] = {
        "name": name,
        "expression": _expr(expression),
        "datatype": datatype,
        "description": description or name.replace("_", " "),
        "default_time_dimension": time,
        "ai_context": {"synonyms": list(synonyms)},
    }
    if filters:
        metric["filters"] = list(filters)
    return metric


def bench_definition(database: str = DATABASE) -> dict[str, Any]:
    return {
        "version": "0.1.1",
        "name": "nova_bench",
        "description": "Benchmark commerce model: orders, customers, products, marketing.",
        "datasets": [
            {
                "name": "orders",
                "source": f"{database}.orders",
                "primary_key": ["order_id"],
                "description": "One row per customer order.",
                "fields": [
                    _field("order_id"),
                    _field("customer_id"),
                    _field("order_date", time=True, synonyms=("order date", "tanggal order")),
                    _field("status", dimension=True, samples=STATUSES,
                           synonyms=("order status", "status order")),
                    _field("sales_channel", dimension=True, samples=CHANNELS,
                           synonyms=("channel", "sales channel", "kanal", "saluran penjualan")),
                    _field("city", "shipping_city", dimension=True, samples=CITIES,
                           synonyms=("kota", "shipping city", "kota pengiriman")),
                    _field("total_amount", datatype="Decimal"),
                ],
            },
            {
                "name": "customers",
                "source": f"{database}.customers",
                "primary_key": ["customer_id"],
                "description": "Customer master data.",
                "fields": [
                    _field("customer_id"),
                    _field("segment", dimension=True, samples=SEGMENTS,
                           synonyms=("customer segment", "segmen", "segmen pelanggan")),
                ],
            },
            {
                "name": "order_items",
                "source": f"{database}.order_items",
                "primary_key": ["order_item_id"],
                "description": "Order line items.",
                "fields": [
                    _field("order_item_id"),
                    _field("order_id"),
                    _field("product_id"),
                    _field("quantity", datatype="Integer"),
                    _field("line_amount", datatype="Decimal"),
                ],
            },
            {
                "name": "products",
                "source": f"{database}.products",
                "primary_key": ["product_id"],
                "description": "Product catalog.",
                "fields": [
                    _field("product_id"),
                    _field("category", dimension=True, samples=CATEGORIES,
                           synonyms=("product category", "kategori", "kategori produk")),
                ],
            },
            {
                "name": "marketing_spend",
                "source": f"{database}.marketing_spend",
                "primary_key": ["spend_id"],
                "description": "Daily marketing spend per channel.",
                "fields": [
                    _field("spend_id"),
                    _field("spend_date", time=True, synonyms=("spend date",)),
                    _field("marketing_channel", "channel", dimension=True, samples=CHANNELS,
                           synonyms=("marketing channel", "kanal marketing")),
                    _field("amount", datatype="Decimal"),
                ],
            },
        ],
        "relationships": [
            {"name": "orders_customers", "from": "orders", "to": "customers",
             "from_columns": ["customer_id"], "to_columns": ["customer_id"],
             "cardinality": "many_to_one"},
            {"name": "items_orders", "from": "order_items", "to": "orders",
             "from_columns": ["order_id"], "to_columns": ["order_id"],
             "cardinality": "many_to_one"},
            {"name": "items_products", "from": "order_items", "to": "products",
             "from_columns": ["product_id"], "to_columns": ["product_id"],
             "cardinality": "many_to_one"},
        ],
        "metrics": [
            _metric("total_revenue", "SUM(orders.total_amount)",
                    synonyms=("revenue", "sales", "total sales", "penjualan", "omzet",
                              "pendapatan"),
                    description="Order revenue in IDR, all statuses."),
            _metric("order_count", "COUNT(DISTINCT orders.order_id)", datatype="Integer",
                    synonyms=("orders", "number of orders", "jumlah order", "pesanan",
                              "jumlah pesanan")),
            _metric("avg_order_value",
                    "SUM(orders.total_amount) / NULLIF(COUNT(DISTINCT orders.order_id), 0)",
                    synonyms=("aov", "average order value", "rata-rata nilai order")),
            _metric("customer_count", "COUNT(DISTINCT orders.customer_id)", datatype="Integer",
                    synonyms=("customers", "buyers", "jumlah pelanggan", "pembeli")),
            _metric("units_sold", "SUM(order_items.quantity)", datatype="Integer",
                    synonyms=("units", "quantity sold", "unit terjual")),
            _metric("product_revenue", "SUM(order_items.line_amount)",
                    synonyms=("product revenue", "pendapatan produk", "line revenue")),
            _metric("marketing_spend_total", "SUM(marketing_spend.amount)", time="spend_date",
                    synonyms=("marketing spend", "ad spend", "biaya marketing",
                              "belanja iklan")),
        ],
        "named_filters": [
            {"name": "completed_order", "expression": "status = 'completed'",
             "dataset": "orders", "synonyms": ["completed", "selesai"]},
        ],
        "conformed_dimensions": [
            {"name": "channel", "fields": ["sales_channel", "marketing_channel"]},
        ],
    }


def bench_model(database: str = DATABASE) -> SemanticModelIR:
    parsed = parse_ossie(yaml.safe_dump(bench_definition(database), sort_keys=False))
    return SemanticModelIR.from_ossie(parsed.as_dict())


def bench_definition_parsed(database: str = DATABASE) -> dict[str, Any]:
    return parse_ossie(yaml.safe_dump(bench_definition(database), sort_keys=False)).as_dict()
