"""Fixture loading through an injected engine executor; no runtime gold resources."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from tests.benchmark.business_intelligence.generate import tables

DATABASE = "NOVA_INTELLIGENCE_BENCH"
Execute = Callable[[str, list | None], Awaitable[dict]]


def column_type(name: str, sample) -> str:
    if name in {
        "unit_price",
        "unit_cost",
        "net_amount",
        "gross_amount",
        "refund_amount",
        "amount",
        "spend",
        "discount",
        "latency_ms",
        "error_rate",
    }:
        return "DECIMAL(22,6)"
    if isinstance(sample, int):
        return "BIGINT"
    if isinstance(sample, float):
        return "DECIMAL(22,6)"
    if name.endswith("_at") or name in {"valid_from", "valid_to"}:
        return "DATETIME"
    return "VARCHAR(64)" if name.endswith("_id") else "VARCHAR(512)"


async def load(execute: Execute, directory: Path, *, replace: bool = False) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest["schema"] != DATABASE:
        raise ValueError("Only the Intelligence benchmark database may be loaded")
    declared = dict(tables(manifest["profile"]))
    if set(declared) != set(manifest["tables"]):
        raise ValueError("Benchmark tables do not match the generator contract")
    for name, metadata in manifest["tables"].items():
        with (directory / f"{name}.csv").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != metadata["sha256"]:
                raise ValueError(f"Fixture integrity failed for {name}")
    await execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE}", None)
    existing = await execute(f"SHOW TABLES FROM {DATABASE}", None)
    if existing["rows"] and not replace:
        raise ValueError("Benchmark database exists; explicitly replace it before freezing a run")
    for name, iterator in declared.items():
        sample = next(iterator)
        columns = list(sample)
        if columns != manifest["tables"][name]["columns"]:
            raise ValueError(f"Unexpected fixture columns for {name}")
        key = columns[0]
        if replace:
            await execute(f"DROP TABLE IF EXISTS {DATABASE}.{name}", None)
        definitions = ",".join(
            f"`{column}` {column_type(column, value)} NOT NULL" for column, value in sample.items()
        )
        await execute(
            f"CREATE TABLE {DATABASE}.{name} ({definitions}) PRIMARY KEY(`{key}`) "
            f"DISTRIBUTED BY HASH(`{key}`) BUCKETS 1 "
            "PROPERTIES('replication_num'='1','enable_persistent_index'='true')",
            None,
        )
        with (directory / f"{name}.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            batch = []
            for row in reader:
                batch.append([row[column] for column in columns])
                if len(batch) == 500:
                    await _insert(execute, name, columns, batch)
                    batch = []
            if batch:
                await _insert(execute, name, columns, batch)
        count = await execute(f"SELECT COUNT(*) FROM {DATABASE}.{name}", None)
        if count["rows"][0][0] != manifest["tables"][name]["rows"]:
            raise ValueError(f"Fixture row count mismatch for {name}")
    return manifest


async def _insert(execute: Execute, name: str, columns: list[str], rows: list[list]) -> None:
    row_params = "(" + ",".join(["%s"] * len(columns)) + ")"
    await execute(
        f"INSERT INTO {DATABASE}.{name} VALUES " + ",".join([row_params] * len(rows)),
        [value for row in rows for value in row],
    )


async def verify_observations(execute: Execute, directory: Path) -> dict:
    """Verify live rows against frozen CSV values, independently of query gold."""
    from datetime import date, datetime
    from decimal import Decimal

    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("schema") != DATABASE:
        raise ValueError("Only benchmark observations can be verified")
    declared = dict(tables(manifest["profile"]))
    if set(declared) != set(manifest["tables"]):
        raise ValueError("Observation table set changed")
    modulus = 1 << 256
    for name, source in declared.items():
        sample = next(source)
        metadata = manifest["tables"][name]
        columns = list(sample)
        if columns != metadata["columns"]:
            raise ValueError("Observation columns changed")
        types = [column_type(key, value) for key, value in sample.items()]

        def row_hash(values, types=types):
            canonical = []
            for value, kind in zip(values, types, strict=True):
                if kind == "BIGINT" or kind.startswith("DECIMAL"):
                    canonical.append(format(Decimal(str(value)).normalize(), "f"))
                elif kind == "DATETIME":
                    instant = (
                        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
                    )
                    canonical.append(instant.isoformat(sep=" "))
                else:
                    canonical.append(value.isoformat() if isinstance(value, date) else str(value))
            return int.from_bytes(
                hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).digest()
            )

        path = directory / f"{name}.csv"
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != metadata["sha256"]:
                raise ValueError(f"Frozen CSV changed: {name}")
        expected, expected_count = 0, 0
        with path.open(newline="") as stream:
            for row in csv.DictReader(stream):
                expected = (expected + row_hash([row[column] for column in columns])) % modulus
                expected_count += 1
        observed, observed_count = 0, 0
        while True:
            result = await execute(
                f"SELECT {','.join(f'`{column}`' for column in columns)} FROM {DATABASE}.{name} "
                f"ORDER BY `{columns[0]}` LIMIT 1000 OFFSET %s",
                [observed_count],
            )
            for row in result["rows"]:
                observed = (observed + row_hash(row)) % modulus
                observed_count += 1
            if len(result["rows"]) < 1000:
                break
        if (observed_count, observed) != (expected_count, expected) or expected_count != metadata[
            "rows"
        ]:
            raise ValueError(f"Live observations differ from the frozen fixture: {name}")
    return manifest
