"""Seeded seven-domain warehouse; evaluator truth is written separately from data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from datetime import date, timedelta
from itertools import chain
from pathlib import Path

SEED = 20260930
START = date(2026, 1, 1)
DAYS = 84
CLOCK = "2026-03-26T00:00:00+00:00"
PROFILES = {
    "small": (1000, 5000, 60),
    "standard": (25000, 150000, 1500),
    "full": (50000, 300000, 3000),
}
CITIES = ("Jakarta", "Bandung", "Surabaya", "Medan")
SCENARIOS = {
    "A": {
        "days": [70, 76],
        "city": "Jakarta",
        "category": "Electronics",
        "injected_driver": "sellable_stockout",
        "marketing_change": 0,
    },
    "B": {
        "days": [63, 66],
        "service": "payment-service",
        "deployment": "pay-v2",
        "injected_driver": "payment_timeout",
    },
    "C": {
        "day": 21,
        "design": "randomized",
        "assignment": "independent",
        "unit": "customer_id",
        "outcome": "converted",
        "population_effect": 0.2,
    },
    "D": {"days": [56, 62], "injected_driver": "discount_and_low_margin_mix"},
    "E": {
        "days": [49, 55],
        "segment": "at_risk",
        "injected_driver": "payment_and_support_deterioration",
    },
    "F": {
        "day": 70,
        "deployment": "catalog-v9",
        "affected_scope": "Bandung",
        "causal_effect_on_jakarta": 0,
    },
}


def _day(index: int) -> str:
    return (START + timedelta(days=index)).isoformat()


def assignments(customers: int):
    rng = random.Random(SEED + 7)
    for customer in range(1, customers + 1):
        arm = int(rng.random() < 0.5)
        converted = int(rng.random() < 0.4 + 0.2 * arm)
        yield {
            "customer_id": customer,
            "campaign_id": 1,
            "assigned_at": _day(21),
            "treatment": arm,
            "converted": converted,
            "session_id": f"experiment-{customer}",
        }


def orders(profile: str):
    customers, count, products = PROFILES[profile]
    rng = random.Random(SEED)
    for ident in range(1, count + 1):
        day = (ident - 1) % DAYS
        city = CITIES[(ident // DAYS) % len(CITIES)]
        product = 1 + rng.randrange(products)
        category = ("Electronics", "Home", "Beauty")[product % 3]
        if city == "Jakarta":
            category, product = "Electronics", 3 * (product // 3) or 3
        quantity = rng.randrange(1, 4) + int((START + timedelta(days=day)).weekday() >= 5)
        price = (80_000, 240_000, 120_000)[product % 3]
        discount = 0.22 if 56 <= day <= 62 else 0.03
        unit_cost = round(price * (0.93 if 56 <= day <= 62 else 0.58), 2)
        if 56 <= day <= 62:
            quantity += 2
        status, reason = "completed", "none"
        draw = rng.random()
        if draw < 0.03:
            status, reason = "fraud_review", "risk_review"
        elif draw < 0.09:
            status, reason = "cancelled", "customer_request"
        elif draw < 0.24:
            status = "fulfilled"
        if city == "Jakarta" and category == "Electronics" and 70 <= day <= 76 and ident % 5 != 0:
            status, reason = "cancelled", "stockout"
        if 63 <= day <= 66 and ident % 3 != 0:
            status, reason = "cancelled", "payment_timeout"
        customer = 1 + rng.randrange(customers)
        if 49 <= day <= 55 and customer % 11 == 0:
            status, reason = "cancelled", "payment_hard_decline"
        gross = round(quantity * price * (1 - discount), 2)
        refund = (
            round(gross * 0.2, 2) if status in {"completed", "fulfilled"} and ident % 17 == 0 else 0
        )
        if day + 1 >= DAYS:
            refund = 0
        yield {
            "order_id": ident,
            "customer_id": customer,
            "ordered_at": _day(day),
            "city": city,
            "category": category,
            "product_id": product,
            "quantity": quantity,
            "price": price,
            "discount": discount,
            "unit_cost": unit_cost,
            "gross_amount": gross,
            "refund_amount": refund,
            "status": status,
            "reason": reason,
            "channel": ("organic", "paid_search", "marketplace")[ident % 3],
            "payment_method": ("QRIS", "GoPay", "Bank Transfer")[ident % 3],
            "day": day,
            "complete": 1,
        }


def tables(profile: str):
    customers, _, products = PROFILES[profile]
    yield (
        "customers",
        (
            {
                "customer_id": i,
                "full_name": (
                    f"{('Siti', 'Budi', 'Dewi', 'Rizky')[i % 4]} "
                    f"{('Santoso', 'Putri', 'Pratama')[i % 3]} {i}"
                ),
                "city": CITIES[i % 4],
                "segment": "at_risk" if i % 11 == 0 else "Growth" if i % 3 else "Loyal",
            }
            for i in range(1, customers + 1)
        ),
    )
    yield (
        "products",
        (
            {
                "product_id": i,
                "name": f"Produk {i}",
                "brand": ("Nusa", "Merapi", "Samudra")[i % 3],
                "category": ("Electronics", "Home", "Beauty")[i % 3],
            }
            for i in range(1, products + 1)
        ),
    )
    yield (
        "orders",
        (
            {
                key: value
                for key, value in row.items()
                if key not in {"quantity", "price", "discount", "unit_cost", "day"}
            }
            for row in orders(profile)
        ),
    )
    yield (
        "order_items",
        (
            {
                "item_id": row["order_id"] * 10 + part,
                "order_id": row["order_id"],
                "product_id": row["product_id"],
                "quantity": 1,
                "unit_price": row["price"],
                "unit_cost": row["unit_cost"],
                "discount": row["discount"],
                "net_amount": round(row["gross_amount"] / row["quantity"], 2),
            }
            for row in orders(profile)
            for part in range(row["quantity"])
        ),
    )
    yield (
        "product_cost_history",
        (
            {
                "cost_id": i * 10 + period,
                "product_id": i,
                "valid_from": _day(start),
                "valid_to": _day(end),
                "unit_cost": round((80_000, 240_000, 120_000)[i % 3] * margin, 2),
            }
            for i in range(1, products + 1)
            for period, (start, end, margin) in enumerate(
                ((0, 56, 0.58), (56, 63, 0.93), (63, 100, 0.58))
            )
        ),
    )
    yield (
        "refunds",
        (
            {
                "refund_id": row["order_id"],
                "order_id": row["order_id"],
                "posted_at": _day(row["day"] + 1),
                "amount": row["refund_amount"],
                "status": "posted",
            }
            for row in orders(profile)
            if row["refund_amount"]
        ),
    )
    yield (
        "warehouses",
        (
            {"warehouse_id": i + 1, "city": city, "capacity": products * 1000}
            for i, city in enumerate(CITIES)
        ),
    )
    yield (
        "inventory_snapshots",
        (
            {
                "snapshot_id": (day * 4 + warehouse) * products + product,
                "observed_at": _day(day),
                "warehouse_id": warehouse + 1,
                "product_id": product,
                "city": CITIES[warehouse],
                "sellable_units": 0
                if warehouse == 0 and product % 3 == 0 and 70 <= day <= 76
                else 100,
                "daily_demand": 25 if product % 3 == 0 else 5,
                "complete": 1,
            }
            for day in range(DAYS)
            for warehouse in range(4)
            for product in range(1, products + 1)
        ),
    )
    yield (
        "stock_movements",
        (
            {
                "movement_id": product,
                "product_id": product,
                "warehouse_id": 1,
                "occurred_at": _day(70),
                "quantity": -100,
                "reason": "supplier_delay",
            }
            for product in range(3, products + 1, 3)
        ),
    )
    yield (
        "fulfillment_events",
        (
            {
                "event_id": row["order_id"],
                "order_id": row["order_id"],
                "occurred_at": _day(row["day"] + 1),
                "status": "shipped" if row["status"] in {"completed", "fulfilled"} else "blocked",
                "reason": row["reason"],
            }
            for row in orders(profile)
            if row["day"] + 1 < DAYS
        ),
    )
    yield (
        "campaigns",
        iter(
            [
                {
                    "campaign_id": 1,
                    "name": "Growth experiment",
                    "channel": "paid_search",
                    "starts_at": _day(21),
                    "objective": "signup_conversion",
                }
            ]
        ),
    )
    yield (
        "campaign_spend",
        (
            {
                "spend_id": day * 4 + city,
                "campaign_id": 1,
                "spent_at": _day(day),
                "city": CITIES[city],
                "spend": 250000,
            }
            for day in range(DAYS)
            for city in range(4)
        ),
    )
    yield "campaign_exposures", assignments(customers)
    yield (
        "web_sessions",
        chain(
            (
                {
                    "session_id": f"order-{row['order_id']}",
                    "customer_id": row["customer_id"],
                    "started_at": row["ordered_at"],
                    "city": row["city"],
                    "channel": row["channel"],
                    "qualified": int(
                        row["status"] in {"completed", "fulfilled"} or row["order_id"] % 7 != 0
                    ),
                }
                for row in orders(profile)
            ),
            (
                {
                    "session_id": row["session_id"],
                    "customer_id": row["customer_id"],
                    "started_at": row["assigned_at"],
                    "city": CITIES[row["customer_id"] % 4],
                    "channel": "paid_search",
                    "qualified": 0,
                }
                for row in assignments(customers)
            ),
        ),
    )
    yield (
        "checkout_events",
        (
            {
                "checkout_id": row["order_id"],
                "session_id": f"order-{row['order_id']}",
                "occurred_at": row["ordered_at"],
                "shipping_selected": int(
                    row["status"] in {"completed", "fulfilled"} or row["order_id"] % 7 != 0
                ),
                "converted": int(row["status"] in {"completed", "fulfilled"}),
                "city": row["city"],
            }
            for row in orders(profile)
        ),
    )
    yield (
        "payment_attempts",
        (
            {
                "attempt_id": row["order_id"],
                "order_id": row["order_id"],
                "attempted_at": row["ordered_at"],
                "method": row["payment_method"],
                "success": int(row["status"] in {"completed", "fulfilled"}),
                "failure_code": row["reason"]
                if row["status"] not in {"completed", "fulfilled"}
                else "none",
                "service_id": "payment-service",
                "city": row["city"],
            }
            for row in orders(profile)
            if row["reason"] not in {"stockout", "customer_request"}
        ),
    )
    yield (
        "payment_failures",
        (
            {
                "failure_id": row["order_id"],
                "attempt_id": row["order_id"],
                "occurred_at": row["ordered_at"],
                "code": row["reason"],
            }
            for row in orders(profile)
            if row["reason"] in {"payment_timeout", "payment_hard_decline", "risk_review"}
        ),
    )
    yield (
        "support_tickets",
        (
            {
                "ticket_id": row["order_id"],
                "customer_id": row["customer_id"],
                "created_at": row["ordered_at"],
                "category": row["reason"],
                "resolution_hours": 72 if row["customer_id"] % 11 == 0 else 12,
            }
            for row in orders(profile)
            if row["status"] == "cancelled"
        ),
    )
    yield (
        "customer_health_events",
        (
            {
                "event_id": i,
                "customer_id": i,
                "occurred_at": _day(55),
                "risk": "high",
                "reason": "payment_and_support",
            }
            for i in sorted(
                {
                    row["customer_id"]
                    for row in orders(profile)
                    if 49 <= row["day"] <= 55 and row["reason"] == "payment_hard_decline"
                }
            )
        ),
    )
    yield (
        "services",
        iter(
            [
                {"service_id": name, "business_function": function}
                for name, function in (
                    ("payment-service", "payment authorization"),
                    ("catalog-service", "product search"),
                )
            ]
        ),
    )
    yield (
        "deployments",
        iter(
            [
                {
                    "deployment_id": "pay-v2",
                    "service_id": "payment-service",
                    "deployed_at": _day(63),
                    "city": "all",
                },
                {
                    "deployment_id": "catalog-v9",
                    "service_id": "catalog-service",
                    "deployed_at": _day(70),
                    "city": "Bandung",
                },
            ]
        ),
    )
    intervals = 24 if profile == "small" else 288
    yield (
        "service_metrics",
        (
            {
                "metric_id": day * intervals * 2 + tick * 2 + service,
                "observed_at": (
                    f"{_day(day)} {tick * 1440 // intervals // 60:02}:"
                    f"{tick * 1440 // intervals % 60:02}:00"
                ),
                "service_id": ("payment-service", "catalog-service")[service],
                "latency_ms": 1400 if service == 0 and 63 <= day <= 66 else 90,
                "error_rate": 0.35 if service == 0 and 63 <= day <= 66 else 0.01,
                "requests": 1000,
                "complete": 1,
            }
            for day in range(DAYS)
            for tick in range(intervals)
            for service in range(2)
        ),
    )
    yield (
        "incidents",
        iter(
            [
                {
                    "incident_id": "payment-incident",
                    "service_id": "payment-service",
                    "starts_at": _day(63),
                    "ends_at": _day(67),
                    "status": "resolved",
                }
            ]
        ),
    )


def generate(destination: Path, profile: str = "small") -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "NOVA_INTELLIGENCE_BENCH",
        "profile": profile,
        "seed": SEED,
        "clock": CLOCK,
        "tables": {},
    }
    for name, iterator in tables(profile):
        first = next(iterator)
        path = destination / f"{name}.csv"
        count = 1
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(first))
            writer.writeheader()
            writer.writerow(first)
            for row in iterator:
                writer.writerow(row)
                count += 1
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest["tables"][name] = {"rows": count, "columns": list(first), "sha256": digest}
    manifest["dataset_hash"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILES, default="small")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluator-output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.resolve() == args.evaluator_output.resolve()
        or args.output.resolve() in args.evaluator_output.resolve().parents
    ):
        parser.error("Evaluator truth must be outside the agent data directory")
    manifest = generate(args.output, args.profile)
    args.evaluator_output.mkdir(parents=True, exist_ok=True)
    (args.evaluator_output / "ground-truth.json").write_text(
        json.dumps({"dataset_hash": manifest["dataset_hash"], "scenarios": SCENARIOS}, indent=2)
        + "\n"
    )
    print(
        json.dumps(
            {
                "profile": args.profile,
                "tables": len(manifest["tables"]),
                "dataset_hash": manifest["dataset_hash"],
            }
        )
    )


if __name__ == "__main__":
    main()
