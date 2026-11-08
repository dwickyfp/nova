"""Load public SQLite databases with declared foreign keys, as answer keys for join discovery.

Each SQLite file becomes one StarRocks database ``NOVA_FK_<NAME>``. Its declared
single-column foreign keys, read with ``PRAGMA foreign_key_list``, are the joins a
discovery run must find; composite keys are left out. The loader follows the
pre-registered protocol (docs/testing/supernova/join-precision-protocol.md):

* a column's type is what its values hold: whole numbers become BIGINT, other
  numbers DOUBLE, everything else VARCHAR; BLOB columns are left out;
* a table above ``SAMPLE_ROWS`` rows that no foreign key targets is sampled
  deterministically, so every remaining child value is still in its parent;
* an identifier Nova refuses (anything but letters, digits and underscores) is
  rewritten with underscores, the same way for a column and the keys that name it.

It writes the answer key as JSON (``db.table.column`` pairs) to stdout. The
engine connection comes from ``STARROCKS_HOST``, ``STARROCKS_PORT``,
``STARROCKS_ROOT_USER`` and ``STARROCKS_ROOT_PASSWORD``; nothing secret is printed.

    python -m tests.benchmark.fk_schemas.load chinook=/path/Chinook.sqlite ... > gold.json
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import asyncmy

SAMPLE_ROWS = 200_000
BATCH = 2_000
TEXT_LIMIT = 60_000


def identifier(name: str) -> str:
    """Nova's accepted identifier for a source name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not cleaned or not re.match(r"[A-Za-z_]", cleaned[0]):
        cleaned = "c_" + cleaned
    return cleaned[:128]


@dataclass
class Table:
    name: str
    columns: list[tuple[str, str]] = field(default_factory=list)  # (source name, type)
    rows: int = 0
    sampled: bool = False


def _type(values: list) -> str | None:
    present = [value for value in values if value is not None]
    if any(isinstance(value, bytes) for value in present):
        return None
    if all(isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"-?\d{1,18}", value))
           for value in present):
        return "BIGINT"
    if all(isinstance(value, (int, float)) for value in present):
        return "DOUBLE"
    return "VARCHAR(65533)"


def read_schema(path: Path) -> tuple[list[Table], list[tuple[str, str]]]:
    """The tables (with types inferred from all values) and the single-column foreign keys."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    tables, keys = [], []
    primary: dict[str, list[str]] = {}
    for name in names:
        info = connection.execute(f'PRAGMA table_info("{name}")').fetchall()
        primary[name] = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]]
        table = Table(name, rows=connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for row in info:
            values = [item[0] for item in connection.execute(
                f'SELECT "{row[1]}" FROM "{name}"')]
            kind = _type(values)
            if kind:
                table.columns.append((row[1], kind))
        tables.append(table)
    lower = {name.lower(): name for name in names}
    for name in names:
        groups: dict[int, list] = {}
        for row in connection.execute(f'PRAGMA foreign_key_list("{name}")'):
            groups.setdefault(row[0], []).append(row)
        for rows in groups.values():
            if len(rows) != 1:
                continue
            _id, _seq, parent, child_column, parent_column = rows[0][:5]
            parent = lower.get(str(parent).lower())
            if parent is None:
                continue
            if parent_column is None:
                if len(primary[parent]) != 1:
                    continue
                parent_column = primary[parent][0]
            keys.append((f"{name}.{child_column}", f"{parent}.{parent_column}"))
    connection.close()
    targets = {pair[1].split(".", 1)[0] for pair in keys}
    for table in tables:
        table.sampled = table.rows > SAMPLE_ROWS and table.name not in targets
    return tables, keys


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)[:TEXT_LIMIT]
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


async def load(name: str, path: Path, connection) -> list[tuple[str, str]]:
    database = "NOVA_FK_" + identifier(name).upper()
    tables, keys = read_schema(path)
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    async with connection.cursor() as cursor:
        await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        await cursor.execute(f"CREATE DATABASE `{database}`")
        for table in tables:
            if not table.columns:
                continue
            ordered = sorted(table.columns, key=lambda item: item[1] == "DOUBLE")
            columns = ", ".join(f"`{identifier(column)}` {kind}" for column, kind in ordered)
            await cursor.execute(
                f"CREATE TABLE `{database}`.`{identifier(table.name)}` ({columns}) "
                "DISTRIBUTED BY RANDOM PROPERTIES('replication_num'='1')")
            select = ", ".join(f'"{column}"' for column, _ in ordered)
            query = f'SELECT {select} FROM "{table.name}"'
            if table.sampled:
                # Deterministic: every k-th row in rowid order.
                step = -(-table.rows // SAMPLE_ROWS)
                query += f" WHERE rowid % {step} = 0"
            rows = source.execute(query)
            while batch := rows.fetchmany(BATCH):
                values = ",".join("(" + ",".join(_literal(v) for v in row) + ")" for row in batch)
                await cursor.execute(
                    f"INSERT INTO `{database}`.`{identifier(table.name)}` VALUES {values}")
    source.close()
    gold = []
    for child, parent in keys:
        (child_table, child_column), (parent_table, parent_column) = (
            child.split(".", 1), parent.split(".", 1))
        gold.append((f"{database}.{identifier(child_table)}.{identifier(child_column)}",
                     f"{database}.{identifier(parent_table)}.{identifier(parent_column)}"))
    sampled = [table.name for table in tables if table.sampled]
    print(f"{database}: {len(tables)} tables, {len(gold)} keys, sampled {sampled}",
          file=sys.stderr)
    return gold


async def main(arguments: list[str]) -> None:
    connection = await asyncmy.connect(
        host=os.environ.get("STARROCKS_HOST", "127.0.0.1"),
        port=int(os.environ.get("STARROCKS_PORT", "9030")),
        user=os.environ.get("STARROCKS_ROOT_USER", "root"),
        password=os.environ.get("STARROCKS_ROOT_PASSWORD", ""),
        autocommit=True,
    )
    try:
        gold: dict[str, list] = {}
        for argument in arguments:
            name, path = argument.split("=", 1)
            gold[name] = await load(name, Path(path), connection)
    finally:
        connection.close()
    json.dump(gold, sys.stdout, indent=1)


if __name__ == "__main__":  # pragma: no cover - needs an engine
    asyncio.run(main(sys.argv[1:]))
