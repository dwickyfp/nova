"""Separate outcome observations revealed only after a recorded decision.

The held-out warehouse is read-only to this fixture. Future rows are synthetic
observations, not evidence that any proposed business action was executed.
"""

from __future__ import annotations

from datetime import date, timedelta

from tests.benchmark.business_intelligence.dataset import DATABASE, column_type
from tests.benchmark.business_intelligence.generate import tables

TRAINING_DATABASE = "NOVA_INTELLIGENCE_OUTCOMES"


async def prepare(execute) -> None:
    """Copy development observations without granting access to this database."""
    await execute(f"CREATE DATABASE IF NOT EXISTS {TRAINING_DATABASE}", None)
    existing = await execute(f"SHOW TABLES FROM {TRAINING_DATABASE}", None)
    if existing["rows"]:
        raise ValueError("Outcome training already exists; use a fresh isolated engine")
    for name, rows in tables("small"):
        sample = next(rows)
        key = next(iter(sample))
        definitions = ",".join(
            f"`{column}` {column_type(column, value)} NOT NULL" for column, value in sample.items()
        )
        await execute(
            f"CREATE TABLE {TRAINING_DATABASE}.{name} ({definitions}) PRIMARY KEY(`{key}`) "
            f"DISTRIBUTED BY HASH(`{key}`) BUCKETS 1 PROPERTIES('replication_num'='1')",
            None,
        )
        await execute(
            f"INSERT INTO {TRAINING_DATABASE}.{name} SELECT * FROM {DATABASE}.{name}",
            None,
        )


async def reveal_future_orders(execute, *, days: int = 7) -> dict:
    """Reveal fixed future orders only in the isolated outcome-learning store."""
    if not 1 <= days <= 14:
        raise ValueError("Outcome fixture supports 1–14 days")
    columns = list(next(dict(tables("small"))["orders"]))
    selected = await execute(
        f"SELECT {','.join('`' + name + '`' for name in columns)} FROM {DATABASE}.orders "
        "WHERE ordered_at >= %s AND ordered_at < %s ORDER BY order_id",
        ["2026-03-24", "2026-03-25"],
    )
    if not selected["rows"]:
        raise ValueError("Seed observation fixtures before revealing outcome data")
    records = []
    start = date(2026, 3, 26)
    for day in range(days):
        for index, values in enumerate(selected["rows"]):
            row = dict(zip(columns, values, strict=True))
            row.update(
                order_id=1_000_000_000_000 + day * 1_000_000 + index,
                ordered_at=(start + timedelta(days=day)).isoformat(),
                status="completed",
                refund_amount=0,
                reason="",
                complete=1,
            )
            records.append([row[column] for column in columns])
    placeholders = "(" + ",".join(["%s"] * len(columns)) + ")"
    column_sql = ",".join("`" + name + "`" for name in columns)
    for offset in range(0, len(records), 500):
        batch = records[offset : offset + 500]
        await execute(
            f"INSERT INTO {TRAINING_DATABASE}.orders ({column_sql}) "
            "VALUES " + ",".join([placeholders] * len(batch)),
            [value for row in batch for value in row],
        )
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(days=days)).isoformat(),
        "rows": len(records),
        "attribution": "observed_after",
    }
