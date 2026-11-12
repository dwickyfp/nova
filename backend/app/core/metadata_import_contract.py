"""Reviewed source-to-target contract for the one-shot metadata import.

``metadata_import/contract.json`` is generated from the reviewed ownership and
column-transform inventories plus the installed PG schema (``generate``). It is
packaged with the backend so the import never reads engineering documents at
run time. ``metadata_import/timestamp_provenance.csv`` records, per DATETIME
column, which writer clock produced the naive source value; a deployment
binding then names the zone for each clock. Nothing here infers a zone.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.metadata_import import ImportColumn, ImportContractError

CONTRACT_DIRECTORY = Path(__file__).resolve().parents[2] / "metadata_import"
CONTRACT_PATH = CONTRACT_DIRECTORY / "contract.json"
PROVENANCE_PATH = CONTRACT_DIRECTORY / "timestamp_provenance.csv"
CONTRACT_FORMAT = "nova-metadata-import-contract/v1"
_TABLE = re.compile(r"[A-Z][A-Z0-9_]{0,62}")
_TARGET = re.compile(r"[a-z][a-z0-9_]{0,62}")

#: Writer clocks a reviewed DATETIME column may carry. ``ENGINE_SESSION`` is the
#: zone Nova pins on its engine connections, ``ENGINE_GLOBAL`` the engine's
#: global zone seen by unpinned raw connections and column defaults.
CLOCKS = ("ENGINE_SESSION", "ENGINE_GLOBAL", "UTC")
#: Classifications that cannot be imported without row-specific reconciliation.
BLOCKED_CLASSES = ("MIXED", "UNKNOWN", "PYTHON_LOCAL")


@dataclass(frozen=True, slots=True)
class SourceColumn:
    name: str
    source_type: str
    nullable: bool
    target: str


@dataclass(frozen=True, slots=True)
class TableContract:
    source_table: str
    owner_domain: str
    key_model: str
    source_present: bool
    source_key: tuple[str, ...]
    columns: tuple[SourceColumn, ...]
    target_table: str
    record_id: bool
    count_query: str | None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def source_types(self) -> tuple[str, ...]:
        return tuple(column.source_type for column in self.columns)

    def key_indexes(self) -> tuple[int, ...]:
        names = self.column_names
        return tuple(names.index(name) for name in self.source_key)


@dataclass(frozen=True, slots=True)
class ImportContract:
    sha256: str
    tables: Mapping[str, TableContract]


def _table_from_json(entry: Mapping[str, Any]) -> TableContract:
    try:
        columns = tuple(
            SourceColumn(c["name"], c["source_type"], bool(c["nullable"]), c["target"])
            for c in entry["columns"]
        )
        table = TableContract(
            source_table=entry["source_table"], owner_domain=entry["owner_domain"],
            key_model=entry["key_model"], source_present=bool(entry["source_present"]),
            source_key=tuple(entry["source_key"]), columns=columns,
            target_table=entry["target_table"], record_id=bool(entry["record_id"]),
            count_query=entry.get("count_query"),
        )
    except (KeyError, TypeError):
        raise ImportContractError() from None
    if (
        not _TABLE.fullmatch(table.source_table) or not _TARGET.fullmatch(table.target_table)
        or table.key_model not in ("PRIMARY", "DUPLICATE", "REVIEW_REQUIRED")
        or any(not _TARGET.fullmatch(column.target) for column in columns)
        or len({column.target for column in columns}) != len(columns)
        or not set(table.source_key) <= set(table.column_names)
        or (table.key_model == "DUPLICATE") != table.record_id
        or (table.key_model == "PRIMARY" and not table.source_key and table.source_present)
    ):
        raise ImportContractError()
    for column in columns:
        ImportColumn(column.name, column.source_type, column.nullable)
    return table


def load_contract(path: Path = CONTRACT_PATH) -> ImportContract:
    try:
        data = path.read_bytes()
        document = json.loads(data)
    except (OSError, ValueError):
        raise ImportContractError() from None
    if not isinstance(document, dict) or document.get("format") != CONTRACT_FORMAT:
        raise ImportContractError()
    tables = {}
    for entry in document.get("tables", []):
        table = _table_from_json(entry)
        if table.source_table in tables:
            raise ImportContractError()
        tables[table.source_table] = table
    if not tables:
        raise ImportContractError()
    return ImportContract(hashlib.sha256(data).hexdigest(), tables)


# ── Timestamp provenance ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ColumnProvenance:
    source_table: str
    source_column: str
    classification: str
    clock: str | None


@dataclass(frozen=True, slots=True)
class TimestampProvenance:
    sha256: str
    columns: Mapping[tuple[str, str], ColumnProvenance]

    def zone_for(self, table: str, column: str, binding: Mapping[str, str]) -> str | None:
        """Return the bound zone, or None when the column needs reconciliation."""
        entry = self.columns.get((table, column))
        if entry is None or entry.clock is None:
            return None
        return binding[entry.clock]


def load_provenance(path: Path = PROVENANCE_PATH) -> TimestampProvenance:
    try:
        data = path.read_bytes()
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    except (OSError, UnicodeDecodeError, csv.Error):
        raise ImportContractError() from None
    columns = {}
    for row in rows:
        try:
            table, column = row["source_table"], row["source_column"]
            classification, clock = row["classification"], row["clock"] or None
        except KeyError:
            raise ImportContractError() from None
        if (
            not _TABLE.fullmatch(table) or not _TARGET.fullmatch(column)
            or (table, column) in columns
            or (clock is not None and clock not in CLOCKS)
            or (clock is None) != (classification in BLOCKED_CLASSES)
        ):
            raise ImportContractError()
        columns[(table, column)] = ColumnProvenance(table, column, classification, clock)
    return TimestampProvenance(hashlib.sha256(data).hexdigest(), columns)


def parse_clock_binding(text: str) -> dict[str, str]:
    """Parse ``ENGINE_SESSION=Asia/Jakarta,ENGINE_GLOBAL=Asia/Jakarta``.

    ``UTC`` is fixed. The operator states the engine zones of the deployment
    whose rows are exported; the exporter refuses a binding that disagrees with
    the engine's current settings, which cannot prove history but exposes drift.
    """
    binding = {"UTC": "UTC"}
    for part in text.split(","):
        name, _, zone = part.strip().partition("=")
        if name not in ("ENGINE_SESSION", "ENGINE_GLOBAL") or name in binding or not zone:
            raise ImportContractError()
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ImportContractError() from None
        binding[name] = zone
    if set(binding) != set(CLOCKS):
        raise ImportContractError()
    return binding


def import_columns(
    table: TableContract, provenance: TimestampProvenance, binding: Mapping[str, str],
) -> tuple[ImportColumn | None, ...]:
    """Typed converters; None marks a DATETIME column without a proven zone."""
    result: list[ImportColumn | None] = []
    for column in table.columns:
        if column.source_type == "datetime":
            zone = provenance.zone_for(table.source_table, column.name, binding)
            result.append(
                None if zone is None else
                ImportColumn(column.name, column.source_type, column.nullable, zone)
            )
        else:
            result.append(ImportColumn(column.name, column.source_type, column.nullable))
    return tuple(result)
