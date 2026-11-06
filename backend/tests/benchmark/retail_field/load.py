"""Load the generated retail field-sales CSVs into StarRocks (``NOVA_RETAIL_FIELD``).

    STARROCKS_FE_MYSQL_PORT=29030 uv run python -m tests.benchmark.retail_field.load \\
        --data /tmp/retail_field [--replace]

Only this lab database is created or replaced. The system pool runs the DDL and
inserts; readers reach the data through their own role and Ranger policies.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from tests.benchmark.retail_field.generate import DATABASE

DECIMAL = {
    "list_price",
    "unit_cost",
    "unit_price",
    "gross_amount",
    "discount_amount",
    "net_amount",
    "cost_amount",
    "planned_distance_km",
    "actual_distance_km",
}
COORDINATES = {"latitude", "longitude", "checkin_latitude", "checkin_longitude"}
INTEGER = {
    "quantity",
    "returned_qty",
    "discount_pct",
    "opening_qty",
    "received_qty",
    "sold_qty",
    "lost_sales_qty",
    "closing_qty",
    "stockout_flag",
    "planned_stops",
    "stop_seq",
    "checkin_distance_m",
    "planned_qty",
    "delivered_qty",
}
#: Key columns per table, first in the CSV.
KEYS = {"outlet_stock_daily": ["stock_date", "outlet_id", "product_id"]}


def column_type(name: str) -> str:
    if name in DECIMAL:
        return "DECIMAL(18,2)"
    if name in COORDINATES:
        return "DOUBLE"
    if name in INTEGER:
        return "INT"
    if name.endswith("_date"):
        return "DATE"
    if name.endswith("_time") or name.endswith("_arrival"):
        return "DATETIME"
    return "VARCHAR(255)"


async def load(directory: Path, *, replace: bool) -> dict[str, int]:
    from app.core.database import db

    await db.init_system_pool()
    try:
        await db.execute_system(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
        existing = await db.execute_system(f"SHOW TABLES FROM {DATABASE}")
        if existing["rows"] and not replace:
            raise SystemExit(f"{DATABASE} already has tables; pass --replace to rebuild it")
        counts = {}
        for path in sorted(directory.glob("*.csv")):
            name = path.stem
            with path.open(newline="") as stream:
                reader = csv.DictReader(stream)
                columns = list(reader.fieldnames or [])
                keys = KEYS.get(name, columns[:1])
                ordered = keys + [column for column in columns if column not in keys]
                await db.execute_system(f"DROP TABLE IF EXISTS {DATABASE}.{name}")
                definitions = ", ".join(f"`{column}` {column_type(column)}" for column in ordered)
                await db.execute_system(
                    f"CREATE TABLE {DATABASE}.{name} ({definitions}) "
                    f"DUPLICATE KEY({', '.join(f'`{key}`' for key in keys)}) "
                    f"DISTRIBUTED BY HASH(`{keys[-1]}`) BUCKETS 2 "
                    "PROPERTIES('replication_num'='1')"
                )
                placeholders = "(" + ",".join(["%s"] * len(ordered)) + ")"
                batch: list[list] = []
                total = 0

                insert = (
                    f"INSERT INTO {DATABASE}.{name} "
                    f"({', '.join(f'`{column}`' for column in ordered)}) VALUES "
                )

                async def flush(
                    rows: list[list], insert: str = insert, row_sql: str = placeholders
                ):
                    await db.execute_system(
                        insert + ",".join([row_sql] * len(rows)),
                        [value for row in rows for value in row],
                    )

                for row in reader:
                    batch.append([row[column] if row[column] != "" else None for column in ordered])
                    if len(batch) == 2000:
                        await flush(batch)
                        total += len(batch)
                        batch = []
                if batch:
                    await flush(batch)
                    total += len(batch)
            loaded = await db.execute_system(f"SELECT COUNT(*) FROM {DATABASE}.{name}")
            counts[name] = int(loaded["rows"][0][0])
            if counts[name] != total:
                raise SystemExit(f"{name}: loaded {counts[name]} of {total} rows")
            print(f"{name}: {counts[name]}")
        return counts
    finally:
        await db.close_system_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    asyncio.run(load(args.data, replace=args.replace))


if __name__ == "__main__":
    main()
