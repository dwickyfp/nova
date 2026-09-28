"""Deterministic ``NOVA_BENCH`` data, anchored to the day it is loaded.

Relative questions ("kuartal lalu", "last 7 days") need data in those windows
whenever the benchmark runs, so dates are generated backwards from ``today``.
A fixed seed keeps the rows identical for a given day. Nothing here reads or
writes any database other than ``NOVA_BENCH``.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal

from tests.benchmark.studio_accuracy.model import (
    CATEGORIES,
    CHANNELS,
    CITIES,
    DATABASE,
    SEGMENTS,
)

SEED = 20260927
DAYS = 800  # a little over two years, so previous_year and YoY always have data

Execute = Callable[[str], Awaitable[object]]


def generate(today: date, *, seed: int = SEED) -> dict[str, list[tuple]]:
    rng = random.Random(seed)
    customers = [
        (index, rng.choice(SEGMENTS), rng.choice(CITIES)) for index in range(1, 301)
    ]
    products = [
        (index, CATEGORIES[(index - 1) % len(CATEGORIES)],
         Decimal(rng.randrange(20, 900) * 1000))
        for index in range(1, 41)
    ]
    orders: list[tuple] = []
    items: list[tuple] = []
    item_id = 1
    order_id = 1
    for offset in range(DAYS, -1, -1):
        day = today - timedelta(days=offset)
        # Growth over time plus a weekly rhythm keeps period comparisons non-trivial.
        base = 4 + (DAYS - offset) // 160 + (2 if day.weekday() in (4, 5) else 0)
        for _ in range(base + rng.randrange(0, 3)):
            customer = rng.choice(customers)
            status = rng.choices(("completed", "cancelled", "pending"), (85, 10, 5))[0]
            channel = rng.choice(CHANNELS)
            city = customer[2] if rng.random() < 0.9 else rng.choice(CITIES)
            total = Decimal(0)
            for _line in range(rng.randrange(1, 4)):
                product = rng.choice(products)
                quantity = rng.randrange(1, 5)
                line = product[2] * quantity
                total += line
                items.append((item_id, order_id, product[0], quantity, line))
                item_id += 1
            orders.append((order_id, customer[0], day, status, channel, city, total))
            order_id += 1
    spend = []
    spend_id = 1
    for offset in range(DAYS, -1, -1):
        day = today - timedelta(days=offset)
        for channel in CHANNELS:
            spend.append((spend_id, day, channel, Decimal(rng.randrange(500, 5000) * 1000)))
            spend_id += 1
    return {
        "customers": [(cid, segment) for cid, segment, _city in customers],
        "products": [(pid, category) for pid, category, _price in products],
        "orders": orders,
        "order_items": items,
        "marketing_spend": spend,
    }


_DDL = {
    "customers": "customer_id INT, segment VARCHAR(32)",
    "products": "product_id INT, category VARCHAR(32)",
    "orders": (
        "order_id BIGINT, customer_id INT, order_date DATE, status VARCHAR(16), "
        "sales_channel VARCHAR(32), shipping_city VARCHAR(32), total_amount DECIMAL(18,2)"
    ),
    "order_items": (
        "order_item_id BIGINT, order_id BIGINT, product_id INT, quantity INT, "
        "line_amount DECIMAL(18,2)"
    ),
    "marketing_spend": "spend_id BIGINT, spend_date DATE, channel VARCHAR(32), "
    "amount DECIMAL(18,2)",
}


def _literal(value: object) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    return str(value)


def statements(today: date, *, database: str = DATABASE, batch: int = 1000) -> list[str]:
    """DDL and INSERT statements that rebuild ``database`` for ``today``."""
    if not database.startswith("NOVA_BENCH"):
        raise ValueError("The benchmark only rebuilds a NOVA_BENCH database.")
    data = generate(today)
    output = [f"DROP DATABASE IF EXISTS {database}", f"CREATE DATABASE {database}"]
    for table, columns in _DDL.items():
        key = columns.split(" ", 1)[0]
        output.append(
            f"CREATE TABLE {database}.{table} ({columns}) DUPLICATE KEY({key}) "
            f"DISTRIBUTED BY HASH({key}) BUCKETS 1 PROPERTIES('replication_num'='1')"
        )
        rows = data[table]
        for start in range(0, len(rows), batch):
            values = ", ".join(
                "(" + ", ".join(_literal(value) for value in row) + ")"
                for row in rows[start:start + batch]
            )
            output.append(f"INSERT INTO {database}.{table} VALUES {values}")
    return output


async def load(execute: Execute, today: date, *, database: str = DATABASE) -> None:
    for statement in statements(today, database=database):
        await execute(statement)
