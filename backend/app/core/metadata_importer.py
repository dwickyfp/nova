"""Resumable, migrator-only import of a verified immutable metadata export."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.core.metadata_cutover import _admit, _connect, _read_state, _translate
from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataOutcomeUnknown,
    MetadataPoolConfig,
)
from app.core.metadata_import import ImportColumn, ImportValueError, import_row_id
from app.core.metadata_import_contract import (
    ImportContract,
    TableContract,
    TimestampProvenance,
    import_columns,
    load_contract,
    load_provenance,
    parse_clock_binding,
)
from app.core.metadata_import_manifest import (
    CONFLICT_FORMAT,
    VALIDATION_FORMAT,
    ExportManifest,
    ManifestError,
    TableDigest,
    canonical_json,
    decode_source_value,
    encode_source_value,
    key_fingerprint,
    load_export_manifest,
    migration_ledger_sha256,
    read_chunk,
    row_fingerprint,
    sha256_bytes,
    write_private,
)
from app.core.metadata_import_payloads import PayloadVerificationError, verify_payloads
from app.core.metadata_import_semantics import verify_semantics
from app.core.metadata_schema import load_migrations


class ImportRefused(ValueError):
    def __init__(self, reason: str, *, table: str | None = None, column: str | None = None):
        self.reason, self.table, self.column = reason, table, column
        location = ".".join(value for value in (table, column) if value)
        super().__init__(
            "Metadata import refused: " + reason + (" (" + location + ")" if location else "")
        )


def _ledger() -> str:
    return migration_ledger_sha256((m.version, m.name, m.checksum) for m in load_migrations())


def _provider(table: TableContract) -> bool:
    return table.source_table == "CONFIG_AI_PROVIDERS"


def _extra_columns(table: TableContract) -> list[str]:
    if _provider(table):
        return ["has_api_key", "api_key_masked"]
    if table.source_table == "CONFIG_STAGES":
        return ["namespace_kind"]
    if table.source_table == "CONFIG_STREAM_SOURCES":
        return ["source_key_columns_text", "source_columns_json_text"]
    return []


def _binding(export: ExportManifest) -> dict[str, str]:
    values = export.document.get("clock_binding", {})
    binding = parse_clock_binding(
        "ENGINE_SESSION="
        + str(values.get("ENGINE_SESSION", ""))
        + ",ENGINE_GLOBAL="
        + str(values.get("ENGINE_GLOBAL", ""))
    )
    if binding != values:
        raise ImportRefused("clock_binding")
    return binding


def converted_row(
    table: TableContract,
    converters: tuple[ImportColumn | None, ...],
    row: list[Any],
) -> list[Any]:
    values: list[Any] = []
    for column, converter, value in zip(table.columns, converters, row, strict=True):
        if converter is None:
            if value is not None or not column.nullable:
                raise ImportRefused(
                    "timestamp_provenance", table=table.source_table, column=column.name
                )
            values.append(None)
        else:
            try:
                values.append(converter.convert(decode_source_value(column.source_type, value)))
            except (ImportValueError, ManifestError):
                raise ImportRefused(
                    "value_conversion", table=table.source_table, column=column.name
                ) from None
    return values


def verify_export(
    export: ExportManifest,
    contract: ImportContract,
    provenance: TimestampProvenance,
) -> dict[str, tuple[ImportColumn | None, ...]]:
    """Verify every byte and conversion before any target data is written."""
    document = export.document
    if document.get("row_identity") != "table-offset/v1":
        raise ImportRefused("row_identity_contract")
    if (
        document.get("contract_sha256") != contract.sha256
        or document.get("timestamp_provenance_sha256") != provenance.sha256
        or set(export.tables) != set(contract.tables)
    ):
        raise ImportRefused("inventory_binding")
    if (
        document.get("consistency") != "FROZEN_STABLE"
        or document.get("unstable_tables")
        or document.get("uncontracted_tables")
    ):
        raise ImportRefused("source_not_complete_and_stable")
    binding = _binding(export)
    converters = {}
    for name, table in contract.tables.items():
        entry = export.tables[name]
        if (
            entry.get("source_present") is not table.source_present
            or entry.get("target_table") != table.target_table
            or entry.get("key_model") != table.key_model
        ):
            raise ImportRefused("table_contract", table=name)
        if not table.source_present:
            if entry.get("status") != "ABSENT" or entry.get("rows") != 0 or entry.get("chunks"):
                raise ImportRefused("absent_table", table=name)
            continue
        if entry.get("status") != "EXPORTED" or entry.get("stable") is not True:
            raise ImportRefused("source_schema_or_stability", table=name)
        converters[name] = import_columns(
            table,
            provenance,
            binding,
            source_row_digest=entry.get("row_digest"),
        )
        digest = TableDigest()
        null_counts = dict.fromkeys(table.column_names, 0)
        files = set()
        for index, chunk in export.chunks(name):
            if chunk.get("file") != f"{name}/{index:06d}.jsonl.gz" or chunk["file"] in files:
                raise ImportRefused("chunk_address", table=name)
            files.add(chunk["file"])
            row_offset = digest.rows
            for row_index, row in enumerate(
                read_chunk(export.directory, chunk, len(table.columns))
            ):
                converted_row(table, converters[name], row)
                if name == "CONFIG_STREAM_SOURCES":
                    for field in ("key_columns", "columns_json"):
                        column = table.columns[table.column_names.index(field)]
                        ImportColumn(field, "json", column.nullable).convert(
                            row[table.column_names.index(field)]
                        )
                if _provider(table):
                    key_value = row[table.column_names.index("api_key")]
                    if key_value is not None and not key_value.startswith("enc:"):
                        raise ImportRefused("provider_ciphertext", table=name, column="api_key")
                key = (
                    [
                        str(
                            import_row_id(
                                export.snapshot_id,
                                name,
                                chunk["sha256"],
                                row_offset + row_index,
                            )
                        )
                    ]
                    if table.record_id
                    else [row[i] for i in table.key_indexes()]
                )
                digest.add(key_fingerprint(key), row_fingerprint(table.source_types, row))
                for column, value in zip(table.columns, row, strict=True):
                    null_counts[column.name] += int(value is None)
        actual = digest.finish()
        if (
            any(entry.get(k) != actual[k] for k in actual)
            or actual["duplicate_keys"]
            or entry.get("null_counts") != null_counts
            or entry.get("validation_count", actual["rows"]) != actual["rows"]
        ):
            raise ImportRefused("source_digest_or_keys", table=name)
    return converters


@asynccontextmanager
async def _unit(connection, freeze_id: UUID):
    async with connection.transaction():
        await _admit(connection)
        await connection.execute("SELECT pg_advisory_xact_lock(%s)", (METADATA_DEPLOYMENT_LOCK,))
        state = await _read_state(connection, lock=True)
        if (
            state.metadata_epoch is not None
            or not state.maintenance
            or state.active_freeze_id != freeze_id
        ):
            raise ImportRefused("freeze_or_epoch")
        yield


async def _columns(connection, contract: ImportContract) -> dict[str, dict[str, str]]:
    cursor = await connection.execute(
        "SELECT c.table_name,c.column_name,c.udt_name,c.is_nullable,c.column_default "
        "FROM information_schema.columns c WHERE c.table_schema='nova_meta'"
    )
    columns: dict[str, dict[str, str]] = {}
    required: dict[str, set[str]] = {}
    for row in await cursor.fetchall():
        name = row["table_name"]
        columns.setdefault(name, {})[row["column_name"]] = row["udt_name"]
        if row["is_nullable"] == "NO" and row["column_default"] is None:
            required.setdefault(name, set()).add(row["column_name"])
    for table in contract.tables.values():
        if table.target_table not in columns:
            raise ImportRefused("target_missing", table=table.source_table)
        if not table.source_present:
            continue
        supplied = {c.target for c in table.columns} | ({"record_id"} if table.record_id else set())
        if _provider(table):
            supplied.remove("api_key")
        supplied.update(_extra_columns(table))
        if (
            not supplied <= set(columns[table.target_table])
            or not required.get(table.target_table, set()) <= supplied
        ):
            raise ImportRefused("target_columns", table=table.source_table)
        for column in table.columns:
            if _provider(table) and column.name == "api_key":
                if columns.get("config_ai_provider_credentials", {}).get("ciphertext") != "text":
                    raise ImportRefused("provider_credentials_schema")
                continue
            kind = columns[table.target_table][column.target]
            allowed = {
                "datetime": {"timestamptz"},
                "json": {"jsonb", "text", "varchar"},
                "int(11)": {"int4", "int8"},
                "bigint(20)": {"int8"},
                "tinyint(1)": {"bool"},
                "double": {"float8"},
            }.get(column.source_type, {"text", "varchar"})
            if table.source_table == "CONFIG_STREAM_SOURCES" and column.name in (
                "key_columns",
                "columns_json",
            ):
                allowed = {"jsonb"}
            if kind not in allowed:
                raise ImportRefused("target_type", table=table.source_table, column=column.name)
    return columns


async def _order(connection, contract: ImportContract) -> list[TableContract]:
    present = {t.target_table: t for t in contract.tables.values() if t.source_present}
    if len(present) != sum(t.source_present for t in contract.tables.values()):
        raise ImportRefused("target_overlap")
    cursor = await connection.execute(
        "SELECT child.relname AS child,parent.relname AS parent FROM pg_constraint c "
        "JOIN pg_class child ON child.oid=c.conrelid "
        "JOIN pg_class parent ON parent.oid=c.confrelid "
        "JOIN pg_namespace n ON n.oid=child.relnamespace "
        "WHERE n.nspname='nova_meta' AND c.contype='f'"
    )
    dependencies: dict[str, set[str]] = {name: set() for name in present}
    for row in await cursor.fetchall():
        if row["child"] in present and row["parent"] in present and row["parent"] != row["child"]:
            dependencies[row["child"]].add(row["parent"])
    ordered = []
    while dependencies:
        ready = sorted(name for name, parents in dependencies.items() if not parents)
        if not ready:
            raise ImportRefused("target_dependency_cycle")
        for name in ready:
            ordered.append(present[name])
            del dependencies[name]
        for parents in dependencies.values():
            parents.difference_update(ready)
    return ordered


def _insert(table: TableContract):
    names = [c.target for c in table.columns if not (_provider(table) and c.name == "api_key")]
    names.extend(_extra_columns(table))
    if table.record_id:
        names.append("record_id")
    return sql.SQL("INSERT INTO nova_meta.{} ({}) VALUES ({})").format(
        sql.Identifier(table.target_table),
        sql.SQL(",").join(map(sql.Identifier, names)),
        sql.SQL(",").join(sql.Placeholder() for _ in names),
    )


async def _target_digest(connection, table, columns, converters):
    fields = [sql.Identifier(c.target) for c in table.columns]
    # Return JSON as text so exact JSON numbers never pass through a Python float.
    for index, column in enumerate(table.columns):
        if column.source_type == "json" and columns[column.target] == "jsonb":
            fields[index] = sql.SQL("{}::text").format(fields[index])
    if table.record_id:
        fields.append(sql.Identifier("record_id"))
    if table.source_table == "CONFIG_STREAM_SOURCES":
        for name in ("key_columns", "columns_json"):
            fields[table.column_names.index(name)] = sql.Identifier("source_" + name + "_text")
    if _provider(table):
        fields[table.column_names.index("api_key")] = sql.SQL(
            "(SELECT c.ciphertext FROM nova_meta.config_ai_provider_credentials c "
            "WHERE c.provider_id=config_ai_providers.id)"
        )
    statement = sql.SQL("SELECT {} FROM nova_meta.{}").format(
        sql.SQL(",").join(fields),
        sql.Identifier(table.target_table),
    )
    digest = TableDigest()
    async with connection.cursor(
        name="nova_import_verify", row_factory=psycopg.rows.tuple_row
    ) as cursor:
        await cursor.execute(statement)
        async for row in cursor:
            encoded = []
            for column, converter, value in zip(table.columns, converters, row, strict=False):
                if value is not None and column.source_type == "datetime":
                    if converter is None or converter.timestamp_zone is None:
                        raise ImportRefused(
                            "target_timestamp", table=table.source_table, column=column.name
                        )
                    value = value.astimezone(ZoneInfo(converter.timestamp_zone)).replace(
                        tzinfo=None
                    )
                encoded.append(encode_source_value(column.source_type, value))
            key = [str(row[-1])] if table.record_id else [encoded[i] for i in table.key_indexes()]
            digest.add(key_fingerprint(key), row_fingerprint(table.source_types, encoded))
    return digest.finish()


async def import_snapshot(
    config: MetadataPoolConfig,
    export: ExportManifest,
    contract: ImportContract,
    provenance: TimestampProvenance,
    *,
    freeze_id: UUID,
    validation_path: Path,
    after_chunk: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """Resume only the same export. Never overwrite a row or retry an uncertain commit."""
    converters = verify_export(export, contract, provenance)
    payloads = await verify_payloads(export, contract)
    run_id = uuid5(export.snapshot_id, export.sha256 + _ledger() + str(freeze_id))
    identity = (
        run_id,
        export.snapshot_id,
        export.sha256,
        contract.sha256,
        provenance.sha256,
        _ledger(),
        len(load_migrations()),
        freeze_id,
    )
    current_source = None
    try:
        async with await _connect(config) as connection:
            async with _unit(connection, freeze_id):
                columns = await _columns(connection, contract)
                ordered = await _order(connection, contract)
                cursor = await connection.execute("SELECT * FROM nova_meta.metadata_import_runs")
                existing = await cursor.fetchall()
                prior_run = None
                if existing:
                    prior_run = existing[0]
                    keys = (
                        "id",
                        "snapshot_id",
                        "export_sha256",
                        "contract_sha256",
                        "provenance_sha256",
                        "ledger_sha256",
                        "schema_version",
                        "freeze_operation_id",
                    )
                    if len(existing) != 1 or tuple(prior_run[key] for key in keys) != identity:
                        raise ImportRefused("different_import_in_progress")
                else:
                    targets = {t.target_table for t in contract.tables.values()}
                    if any(_provider(t) for t in contract.tables.values()):
                        targets.add("config_ai_provider_credentials")
                    for name in sorted(targets):
                        cursor = await connection.execute(
                            sql.SQL("SELECT EXISTS(SELECT 1 FROM nova_meta.{}) AS occupied").format(
                                sql.Identifier(name)
                            )
                        )
                        occupancy = await cursor.fetchone()
                        if occupancy is None or occupancy["occupied"]:
                            raise ImportRefused("target_not_empty")
                    await connection.execute(
                        "INSERT INTO nova_meta.metadata_import_runs (id,snapshot_id,export_sha256,"
                        "contract_sha256,provenance_sha256,ledger_sha256,"
                        "schema_version,freeze_operation_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        identity,
                    )
            for table in ordered:
                name = table.source_table
                current_source = name
                row_offset = 0
                for index, chunk in export.chunks(name):
                    raw_rows = read_chunk(export.directory, chunk, len(table.columns))
                    async with _unit(connection, freeze_id):
                        cursor = await connection.execute(
                            "SELECT chunk_sha256,row_count FROM nova_meta.metadata_import_chunks "
                            "WHERE run_id=%s AND source_table=%s AND chunk_index=%s",
                            (run_id, name, index),
                        )
                        receipt = await cursor.fetchone()
                        if receipt:
                            if (receipt["chunk_sha256"], receipt["row_count"]) != (
                                chunk["sha256"],
                                len(raw_rows),
                            ):
                                raise ImportRefused("chunk_receipt_mismatch", table=name)
                            row_offset += len(raw_rows)
                            continue
                        parameters = []
                        credentials = []
                        for row_index, row in enumerate(raw_rows):
                            values = converted_row(table, converters[name], row)
                            for position, column in enumerate(table.columns):
                                if (
                                    isinstance(values[position], Jsonb)
                                    and columns[table.target_table][column.target] != "jsonb"
                                ):
                                    values[position] = row[position]
                            if _provider(table):
                                ciphertext = values.pop(table.column_names.index("api_key"))
                                credentials.append(
                                    (row[table.column_names.index("id")], ciphertext)
                                )
                                values.extend(
                                    [
                                        ciphertext is not None,
                                        "********" if ciphertext is not None else None,
                                    ]
                                )
                            if table.source_table == "CONFIG_STAGES":
                                values.append("LEGACY")
                            if name == "CONFIG_STREAM_SOURCES":
                                for field in ("key_columns", "columns_json"):
                                    position = table.column_names.index(field)
                                    values.append(row[position])
                                    values[position] = ImportColumn(
                                        field, "json", table.columns[position].nullable
                                    ).convert(row[position])
                            if table.record_id:
                                values.append(
                                    import_row_id(
                                        export.snapshot_id,
                                        name,
                                        chunk["sha256"],
                                        row_offset + row_index,
                                    )
                                )
                            parameters.append(values)
                        async with connection.cursor() as cursor:
                            await cursor.executemany(_insert(table), parameters)
                            if credentials:
                                await cursor.executemany(
                                    "INSERT INTO nova_meta.config_ai_provider_credentials "
                                    "(provider_id,ciphertext,metadata_revision) VALUES (%s,%s,1)",
                                    credentials,
                                )
                        await connection.execute(
                            "INSERT INTO nova_meta.metadata_import_chunks "
                            "(run_id,source_table,chunk_index,chunk_sha256,row_count) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (run_id, name, index, chunk["sha256"], len(raw_rows)),
                        )
                    if after_chunk:
                        after_chunk(name, index)
                    row_offset += len(raw_rows)
            if await verify_payloads(export, contract) != payloads:
                raise ImportRefused("payload_changed_during_import")
            async with _unit(connection, freeze_id):
                tables = {}
                for name, table in contract.tables.items():
                    source = export.tables[name]
                    target = (
                        await _target_digest(
                            connection,
                            table,
                            columns[table.target_table],
                            converters[name],
                        )
                        if table.source_present
                        else TableDigest().finish()
                    )
                    expected = source if table.source_present else TableDigest().finish()
                    if any(target[k] != expected[k] for k in target):
                        raise ImportRefused("target_digest_mismatch", table=name)
                    tables[name] = {
                        "status": "MATCHED" if table.source_present else "ABSENT",
                        "target_table": table.target_table,
                        "source_count": expected["rows"],
                        "target_count": target["rows"],
                        **{
                            side + "_" + k: target[k]
                            for side in ("source", "target")
                            for k in ("key_digest", "row_digest")
                        },
                    }
                semantics = await verify_semantics(connection)
                if semantics["status"] != "VERIFIED":
                    check = next(name for name, count in semantics["checks"].items() if count)
                    raise ImportRefused("semantic_" + check)
                manifest = {
                    "format": VALIDATION_FORMAT,
                    "status": "VALIDATED",
                    "schema_version": len(load_migrations()),
                    "migration_ledger_sha256": _ledger(),
                    "snapshot_id": str(export.snapshot_id),
                    "import_run_id": str(run_id),
                    "export_manifest_sha256": export.sha256,
                    "contract_sha256": contract.sha256,
                    "timestamp_provenance_sha256": provenance.sha256,
                    "freeze_operation_id": str(freeze_id),
                    "validated_at": datetime.now(UTC).isoformat(),
                    "source_consistency": "FROZEN_STABLE",
                    "conflicts": {"blocking": 0},
                    "managed_payloads": payloads,
                    "semantics": semantics,
                    "tables": tables,
                }
                cursor = await connection.execute(
                    "SELECT status,validation_document FROM nova_meta.metadata_import_runs "
                    "WHERE id=%s",
                    (run_id,),
                )
                prior_run = await cursor.fetchone()
                if prior_run and prior_run["status"] == "VALIDATED":
                    document = bytes(prior_run["validation_document"])
                    manifest = json.loads(document)
                    if (manifest["tables"] != tables or manifest["managed_payloads"] != payloads
                            or manifest["semantics"] != semantics):
                        raise ImportRefused("prior_validation_mismatch")
                else:
                    document = canonical_json(manifest)
                    await connection.execute(
                        "UPDATE nova_meta.metadata_import_runs SET status='VALIDATED',"
                        "validation_sha256=%s,validation_document=%s,"
                        "validated_at=%s WHERE id=%s",
                        (sha256_bytes(document), document, datetime.now(UTC), run_id),
                    )
            write_private(validation_path, document)
            return manifest
    except psycopg.Error as error:
        if isinstance(error, psycopg.OperationalError):
            raise _translate(error) from None
        raise ImportRefused(
            "database_" + (error.sqlstate or "unknown"), table=current_source
        ) from None


def main() -> int:
    from app.core.config import MetadataMigratorSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--freeze-operation-id", type=UUID, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = MetadataMigratorSettings()  # type: ignore[call-arg]
        config = MetadataPoolConfig(settings.dsn.get_secret_value())
        result = asyncio.run(
            import_snapshot(
                config,
                load_export_manifest(args.manifest, args.manifest_sha256),
                load_contract(),
                load_provenance(),
                freeze_id=args.freeze_operation_id,
                validation_path=args.validation,
            )
        )
    except (ImportRefused, PayloadVerificationError) as error:
        conflict = {
            "status": "REFUSED",
            "reason": error.reason,
            "table": error.table,
            "column": error.column if isinstance(error, ImportRefused) else None,
            "row_address": error.row_address
            if isinstance(error, PayloadVerificationError)
            else None,
        }
        write_private(
            args.validation.with_suffix(".conflicts.json"),
            canonical_json({"format": CONFLICT_FORMAT, "blocking": 1, "conflicts": [conflict]}),
        )
        print(json.dumps(conflict))
        return 1
    except MetadataOutcomeUnknown:
        print(
            json.dumps(
                {
                    "status": "OUTCOME_UNKNOWN",
                    "manifest_sha256": args.manifest_sha256,
                    "next": (
                        "Reconnect with the same immutable export; "
                        "resume verifies committed chunk receipts."
                    ),
                }
            )
        )
        return 1
    except Exception as error:  # noqa: BLE001 - driver/configuration errors must not echo values
        print(json.dumps({"status": "REFUSED", "reason": type(error).__name__}))
        return 1
    print(json.dumps({"status": result["status"], "import_run_id": result["import_run_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
