"""Read-only snapshot export of every contracted ``NOVA_SYSTEM`` table.

The exporter issues only ``SELECT``/``SHOW`` statements from a fixed set of
templates. StarRocks offers no cross-table snapshot, so consistency is proven
instead: every partition's visible version is captured before and after the
read; an unchanged version set means no write committed during the export.
At cutover the writers are frozen and every table must be stable; a rehearsal
against a live system records the tables that moved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.metadata_import import ImportContractError, import_row_id
from app.core.metadata_import_contract import (
    ImportContract,
    TableContract,
    TimestampProvenance,
    load_contract,
    load_provenance,
    parse_clock_binding,
)
from app.core.metadata_import_manifest import (
    EXPORT_FORMAT,
    ManifestError,
    TableDigest,
    canonical_json,
    chunk_name,
    encode_chunk,
    encode_source_value,
    key_fingerprint,
    private_directory,
    row_fingerprint,
    sha256_bytes,
    write_chunk,
    write_private,
)

SOURCE_DATABASE = "NOVA_SYSTEM"
DEFAULT_CHUNK_ROWS = 5000
_READ_ONLY = re.compile(r"(SELECT|SHOW) ", re.IGNORECASE)
_COUNT_QUERY = re.compile(r"SELECT COUNT\(\*\) FROM NOVA_SYSTEM\.`([A-Z][A-Z0-9_]{0,62})`")


class ExportRefused(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Metadata export refused: {reason}")


class SourceCursor(Protocol):
    description: Any

    async def execute(self, query: str, args: Any = None) -> Any: ...
    async def fetchall(self) -> Sequence[Sequence[Any]]: ...
    async def fetchmany(self, size: int) -> Sequence[Sequence[Any]]: ...


class SourceReader:
    """A connection wrapper that can only run read statements."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def query(self, statement: str, args: Any = None) -> list[tuple[Any, ...]]:
        if not _READ_ONLY.match(statement):
            raise ExportRefused("non_read_statement")
        async with self._connection.cursor() as cursor:
            await cursor.execute(statement, args)
            return [tuple(row) for row in await cursor.fetchall()]

    async def stream(self, statement: str, batch: int):
        if not _READ_ONLY.match(statement):
            raise ExportRefused("non_read_statement")
        from asyncmy.cursors import SSCursor

        async with self._connection.cursor(SSCursor) as cursor:
            await cursor.execute(statement)
            while rows := await cursor.fetchmany(batch):
                yield [tuple(row) for row in rows]


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name):
        raise ExportRefused("identifier")
    return f"`{name}`"


async def _partition_versions(reader: SourceReader, table: str) -> str:
    rows = await reader.query(f"SHOW PARTITIONS FROM {SOURCE_DATABASE}.{_ident(table)}")
    # PartitionId, PartitionName, VisibleVersion: the committed data version.
    versions = sorted((str(row[0]), str(row[1]), str(row[2])) for row in rows)
    return sha256_bytes(canonical_json(versions))


async def _source_columns(reader: SourceReader) -> dict[str, list[tuple[str, str, bool]]]:
    rows = await reader.query(
        "SELECT table_name, column_name, column_type, is_nullable, ordinal_position "
        "FROM information_schema.columns WHERE table_schema = %s",
        (SOURCE_DATABASE,),
    )
    tables: dict[str, list[tuple[int, str, str, bool]]] = {}
    for table, column, column_type, nullable, ordinal in rows:
        tables.setdefault(table, []).append((int(ordinal), column, column_type, nullable == "YES"))
    return {
        table: [(name, kind, nullable) for _, name, kind, nullable in sorted(columns)]
        for table, columns in tables.items()
    }


def _order_by(table: TableContract) -> str:
    # PRIMARY keys give a total order; duplicate tables order by every
    # orderable column so a re-export of unchanged data has stable chunks.
    names = (
        table.source_key
        if table.key_model == "PRIMARY" and table.source_key
        else tuple(column.name for column in table.columns if column.source_type != "json")
    )
    return ", ".join(_ident(name) for name in names)


async def _export_table(
    reader: SourceReader,
    table: TableContract,
    directory: Path,
    snapshot_id: UUID,
    chunk_rows: int,
) -> dict[str, Any]:
    columns = ", ".join(_ident(name) for name in table.column_names)
    statement = (
        f"SELECT {columns} FROM {SOURCE_DATABASE}.{_ident(table.source_table)} "
        f"ORDER BY {_order_by(table)}"
    )
    types = table.source_types
    key_indexes = table.key_indexes()
    digest = TableDigest()
    chunks: list[dict[str, Any]] = []
    pending: list[list[Any]] = []
    null_counts = [0] * len(types)

    def flush() -> None:
        payload = encode_chunk(pending)
        entry = write_chunk(directory, chunk_name(table.source_table, len(chunks)), payload)
        entry["rows"] = len(pending)
        for index, row in enumerate(pending):
            if table.record_id:
                key = [str(import_row_id(snapshot_id, table.source_table, entry["sha256"], index))]
            else:
                key = [row[position] for position in key_indexes]
            digest.add(key_fingerprint(key), row_fingerprint(types, row))
        chunks.append(entry)
        pending.clear()

    async for rows in reader.stream(statement, 1000):
        for row in rows:
            if len(row) != len(types):
                raise ExportRefused("row_width")
            encoded = [
                encode_source_value(kind, value) for kind, value in zip(types, row, strict=True)
            ]
            for position, value in enumerate(encoded):
                if value is None:
                    null_counts[position] += 1
            pending.append(encoded)
            if len(pending) >= chunk_rows:
                flush()
    if pending:
        flush()
    result = digest.finish()
    result["chunks"] = chunks
    result["null_counts"] = dict(zip(table.column_names, null_counts, strict=True))
    return result


async def export_snapshot(
    reader: SourceReader,
    contract: ImportContract,
    provenance: TimestampProvenance,
    binding: Mapping[str, str],
    directory: Path,
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    snapshot_id: UUID | None = None,
    require_stable: bool = True,
) -> tuple[Path, str]:
    """Write chunks and a canonical manifest; return its path and SHA-256."""
    if not 1 <= chunk_rows <= 100_000:
        raise ExportRefused("chunk_rows")
    snapshot_id = snapshot_id or uuid4()
    private_directory(directory)
    if any(directory.iterdir()):
        raise ExportRefused("export_directory_not_empty")
    started = time.monotonic()
    zones = await reader.query("SELECT @@time_zone, @@global.time_zone, current_version()")
    session_zone, global_zone, version = zones[0]
    # A binding that disagrees with the engine's present settings is drift.
    if binding["ENGINE_GLOBAL"] != global_zone or binding["ENGINE_SESSION"] != session_zone:
        raise ExportRefused("clock_binding_differs_from_engine")
    present = {row[0] for row in await reader.query(f"SHOW TABLES FROM {SOURCE_DATABASE}")}
    observed = await _source_columns(reader)
    contracted = set(contract.tables)
    before = {
        name: await _partition_versions(reader, name) for name in sorted(contracted & present)
    }
    tables: dict[str, Any] = {}
    for name, table in sorted(contract.tables.items()):
        entry: dict[str, Any] = {
            "source_present": name in present,
            "key_model": table.key_model,
            "target_table": table.target_table,
        }
        if name not in present:
            entry.update(status="ABSENT", rows=0, chunks=[])
            tables[name] = entry
            continue
        expected = [(c.name, c.source_type, c.nullable) for c in table.columns]
        if not table.source_present or observed.get(name) != expected:
            # A table the contract expects absent, or with drifted columns,
            # cannot be imported until the contract is reviewed again.
            entry.update(
                status="SCHEMA_DRIFT",
                rows=None,
                chunks=[],
                observed_columns=len(observed.get(name, [])),
            )
            tables[name] = entry
            continue
        table_started = time.monotonic()
        entry.update(await _export_table(reader, table, directory, snapshot_id, chunk_rows))
        if table.count_query:
            match = _COUNT_QUERY.fullmatch(table.count_query)
            if not match or match[1] != name:
                raise ExportRefused("validation_query")
            entry["validation_count"] = int((await reader.query(table.count_query))[0][0])
        entry["seconds"] = round(time.monotonic() - table_started, 3)
        entry["status"] = "EXPORTED"
        tables[name] = entry
    after = {name: await _partition_versions(reader, name) for name in before}
    unstable = sorted(name for name in before if before[name] != after[name])
    for name in before:
        tables[name]["partition_versions_sha256"] = after[name]
        tables[name]["stable"] = name not in unstable
        if tables[name].get("validation_count") not in (None, tables[name].get("rows")):
            unstable = sorted({*unstable, name})
            tables[name]["stable"] = False
    uncontracted = sorted(present - contracted)
    consistency = "FROZEN_STABLE" if not unstable else "LIVE_UNSTABLE"
    if require_stable and unstable:
        raise ExportRefused("source_changed_during_export")
    document = {
        "format": EXPORT_FORMAT,
        "snapshot_id": str(snapshot_id),
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            "database": SOURCE_DATABASE,
            "engine_version": version,
            "session_time_zone": session_zone,
            "global_time_zone": global_zone,
        },
        "contract_sha256": contract.sha256,
        "timestamp_provenance_sha256": provenance.sha256,
        "clock_binding": dict(sorted(binding.items())),
        "chunk_rows": chunk_rows,
        "consistency": consistency,
        "unstable_tables": unstable,
        "uncontracted_tables": uncontracted,
        "seconds": round(time.monotonic() - started, 3),
        "tables": tables,
    }
    data = canonical_json(document)
    path = directory / "manifest.json"
    write_private(path, data)
    return path, hashlib.sha256(data).hexdigest()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    import asyncmy

    from app.core.config import settings
    from app.core.database import _timezone_init_command

    contract = load_contract()
    provenance = load_provenance()
    binding = parse_clock_binding(args.clock_binding)
    connection = await asyncmy.connect(
        host=settings.STARROCKS_HOST,
        port=settings.STARROCKS_FE_MYSQL_PORT,
        user=settings.STARROCKS_ROOT_USER,
        password=settings.STARROCKS_ROOT_PASSWORD,
        autocommit=True,
        connect_timeout=10,
        init_command=_timezone_init_command(),
    )
    try:
        reader = SourceReader(connection)
        # information_schema planning is slow on a busy FE; reads stay read-only.
        async with connection.cursor() as cursor:
            await cursor.execute("SET new_planner_optimize_timeout = 120000")
        path, digest = await export_snapshot(
            reader,
            contract,
            provenance,
            binding,
            Path(args.output),
            chunk_rows=args.chunk_rows,
            require_stable=not args.allow_live_source,
        )
    finally:
        connection.close()
    document = json.loads(path.read_bytes())
    return {
        "status": "EXPORTED",
        "manifest_sha256": digest,
        "snapshot_id": document["snapshot_id"],
        "consistency": document["consistency"],
        "tables": len(document["tables"]),
        "rows": sum(entry.get("rows") or 0 for entry in document["tables"].values()),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True)
    parser.add_argument("--clock-binding", required=True)
    parser.add_argument("--chunk-rows", type=int, default=DEFAULT_CHUNK_ROWS)
    parser.add_argument(
        "--allow-live-source",
        action="store_true",
        help="Rehearsal only: record moving tables instead of refusing the export.",
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except (ExportRefused, ManifestError) as error:
        print(json.dumps({"status": "REFUSED", "error": error.reason}))
        return 1
    except ImportContractError:
        print(json.dumps({"status": "REFUSED", "error": "import_contract"}))
        return 1
    except Exception as error:  # noqa: BLE001 - driver errors may echo values
        print(json.dumps({"status": "REFUSED", "error": type(error).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
