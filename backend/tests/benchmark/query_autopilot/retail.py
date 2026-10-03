"""Seeded retail data with correlated baskets, seasonality, skew and campaigns."""

from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Scale:
    customers: int
    orders: int
    items: int
    products: int = 1000
    stores: int = 30
    campaigns: int = 24


SCALES = {
    "ci": Scale(100, 500, 1500, 100, 5, 12),
    "local": Scale(10000, 50000, 150000),
    "medium": Scale(100000, 500000, 2000000, 5000, 100, 48),
    "large": Scale(500000, 2000000, 8000000, 20000, 500, 96),
}
SCHEMA = {
    "customers": "customer_id BIGINT, segment VARCHAR(32), region VARCHAR(32), signup_date DATE",
    "product_categories": "category_id INT, category_name VARCHAR(64)",
    "products": "product_id BIGINT, category_id INT, price DECIMAL(12,2), is_active BOOLEAN",
    "stores": "store_id INT, region VARCHAR(32), opened_date DATE",
    "orders": (
        "order_id BIGINT, customer_id BIGINT, store_id INT, ordered_at DATETIME, "
        "status VARCHAR(32), total DECIMAL(16,2)"
    ),
    "order_items": (
        "item_id BIGINT, order_id BIGINT, product_id BIGINT, quantity INT, "
        "unit_price DECIMAL(12,2), discount DECIMAL(12,2)"
    ),
    "payments": (
        "payment_id BIGINT, order_id BIGINT, amount DECIMAL(16,2), status "
        "VARCHAR(32), method VARCHAR(32)"
    ),
    "campaigns": (
        "campaign_id INT, channel VARCHAR(32), start_date DATE, end_date DATE, budget DECIMAL(16,2)"
    ),
    "campaign_attribution": (
        "attribution_id BIGINT, order_id BIGINT, campaign_id INT, weight DECIMAL(8,4)"
    ),
}


def generate(directory: Path, scale: Scale, *, seed: int = 20261001) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    origin = datetime(2025, 1, 1)
    regions = ("west", "central", "east", "north")
    counts = {}

    def write(table, rows):
        count = 0
        with (directory / f"{table}.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            for row in rows:
                writer.writerow(row)
                count += 1
        counts[table] = count

    write(
        "customers",
        (
            (
                i,
                rng.choices(("consumer", "business", "vip"), (80, 15, 5))[0],
                rng.choice(regions),
                (origin - timedelta(days=rng.randrange(730))).date(),
            )
            for i in range(1, scale.customers + 1)
        ),
    )
    write("product_categories", ((i, f"category-{i:02d}") for i in range(1, 21)))
    prices = {i: Decimal(rng.randrange(199, 50000)) / 100 for i in range(1, scale.products + 1)}
    write("products", ((i, 1 + i % 20, prices[i], int(i % 19 != 0)) for i in prices))
    write(
        "stores",
        (
            (i, regions[i % 4], (origin - timedelta(days=i * 30)).date())
            for i in range(1, scale.stores + 1)
        ),
    )
    campaigns = {
        i: origin + timedelta(days=(i - 1) * 365 // scale.campaigns)
        for i in range(1, scale.campaigns + 1)
    }
    write(
        "campaigns",
        (
            (
                i,
                ("email", "search", "social")[i % 3],
                start.date(),
                (start + timedelta(days=14)).date(),
                10000 + i * 125,
            )
            for i, start in campaigns.items()
        ),
    )
    totals = [Decimal(0)] * (scale.orders + 1)

    def items():
        for i in range(1, scale.items + 1):
            order = 1 + (i - 1) % scale.orders
            product = min(scale.products, 1 + int(rng.random() ** 3 * scale.products))
            quantity = rng.choices((1, 2, 3, 10), (65, 20, 12, 3))[0]
            discount = (
                (prices[product] * Decimal("0.10")).quantize(Decimal("0.01"))
                if order % 5 == 0
                else Decimal(0)
            )
            totals[order] += quantity * (prices[product] - discount)
            yield i, order, product, quantity, prices[product], discount

    write("order_items", items())
    attribution = []
    statuses = {}

    def orders():
        for i in range(1, scale.orders + 1):
            # The increasing density toward year end models growth; campaign
            # orders cluster within their campaign's actual active interval.
            campaign = 1 + i % scale.campaigns
            day = int(rng.random() ** 0.65 * 365)
            timestamp = (
                campaigns[campaign] + timedelta(days=rng.randrange(14))
                if i % 5 == 0
                else origin + timedelta(days=day)
            )
            timestamp += timedelta(
                hours=rng.choices((9, 12, 18, 21), (10, 25, 40, 25))[0], minutes=rng.randrange(60)
            )
            customer = min(scale.customers, 1 + int(rng.random() ** 2 * scale.customers))
            status = rng.choices(("completed", "returned", "cancelled", "pending"), (85, 5, 3, 7))[
                0
            ]
            statuses[i] = status
            if i % 5 == 0:
                attribution.append((len(attribution) + 1, i, campaign, Decimal("1.0000")))
            yield i, customer, 1 + i % scale.stores, timestamp.isoformat(sep=" "), status, totals[i]

    write("orders", orders())
    write(
        "payments",
        (
            (
                i,
                i,
                totals[i],
                "captured"
                if statuses[i] == "completed"
                else "refunded"
                if statuses[i] == "returned"
                else "pending",
                ("card", "transfer", "wallet")[i % 3],
            )
            for i in range(1, scale.orders + 1)
        ),
    )
    write("campaign_attribution", attribution)
    metadata = {
        "seed": seed,
        "scale": asdict(scale),
        "tables": counts,
        "synthetic": True,
        "origin": origin.isoformat(),
        "effects": [
            "customer_and_product_skew",
            "evening_seasonality",
            "annual_growth",
            "campaign_windows",
            "correlated_payments_and_baskets",
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--scale", choices=SCALES, default="local")
    parser.add_argument("--seed", type=int, default=20261001)
    args = parser.parse_args()
    print(json.dumps(generate(args.directory, SCALES[args.scale], seed=args.seed), indent=2))
