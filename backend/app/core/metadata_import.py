"""Lossless value conversion and row identities for immutable metadata exports.

This module performs no database I/O. A caller must supply the reviewed source
column contract and timestamp provenance; it cannot infer them from row values.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb

_VARCHAR = re.compile(r"varchar\(([1-9][0-9]{0,6})\)")
_TABLE = re.compile(r"[A-Z][A-Z0-9_]{0,62}")
_COLUMN = re.compile(r"[a-z][a-z0-9_]{0,62}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FIXED_TYPES = {
    "int(11)": "integer", "bigint(20)": "bigint", "tinyint(1)": "boolean",
    "double": "double precision", "json": "jsonb", "datetime": "timestamptz",
}
_COLUMN_TARGETS = {
    ("CONFIG_AI_PROVIDERS", "api_key"): ("varchar(512)", "text"),
}


class ImportContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("Metadata import contract is missing or invalid")


class ImportValueError(ValueError):
    def __init__(self) -> None:
        super().__init__("Metadata value cannot be imported without loss or ambiguity")


def target_type(
    source_type: str, *, source_table: str | None = None, source_column: str | None = None,
) -> str:
    """Map observed source types, including explicitly reviewed column overrides."""
    if not isinstance(source_type, str):
        raise ImportContractError()
    if source_table is not None or source_column is not None:
        if (
            not isinstance(source_table, str) or not _TABLE.fullmatch(source_table)
            or not isinstance(source_column, str) or not _COLUMN.fullmatch(source_column)
        ):
            raise ImportContractError()
        override = _COLUMN_TARGETS.get((source_table, source_column))
        if override:
            expected_source, destination = override
            if source_type != expected_source:
                raise ImportContractError()
            return destination
    if source_type in _FIXED_TYPES:
        return _FIXED_TYPES[source_type]
    match = _VARCHAR.fullmatch(source_type)
    if match and int(match[1]) <= 1_048_576:
        return "text" if int(match[1]) >= 65533 else source_type
    raise ImportContractError()


def _text(value: Any) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ImportValueError()
    value.encode("utf-8", errors="strict")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportValueError()
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> Any:
    raise ImportValueError()


def _verify_json_strings(value: Any) -> None:
    if isinstance(value, str):
        _text(value)
    elif isinstance(value, list):
        for item in value:
            _verify_json_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _text(key)
            _verify_json_strings(item)


def _json_document(value: Any) -> str:
    """Validate without converting exact JSON numbers through binary floats."""
    text = _text(value)
    decoded = json.loads(
        text, parse_int=Decimal, parse_float=Decimal,
        object_pairs_hook=_unique_json_object, parse_constant=_invalid_json_constant,
    )
    _verify_json_strings(decoded)
    return text


def _raw_json_dump(value: str) -> str:
    return value


def _timestamp(value: Any, zone_name: str | None) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is not None or zone_name is None:
        raise ImportValueError()
    zone = ZoneInfo(zone_name)
    first = value.replace(tzinfo=zone, fold=0).astimezone(UTC)
    second = value.replace(tzinfo=zone, fold=1).astimezone(UTC)
    first_valid = first.astimezone(zone).replace(tzinfo=None) == value
    second_valid = second.astimezone(zone).replace(tzinfo=None) == value
    if not first_valid or (second_valid and first != second):
        # A local gap has no instant; a repeated local time needs row-specific
        # provenance. Neither can be repaired by choosing a default fold.
        raise ImportValueError()
    return first


@dataclass(frozen=True, slots=True)
class ImportColumn:
    name: str
    source_type: str
    nullable: bool
    timestamp_zone: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str) or not _COLUMN.fullmatch(self.name)
            or type(self.nullable) is not bool
        ):
            raise ImportContractError()
        target_type(self.source_type)
        if self.timestamp_zone is not None:
            if self.source_type != "datetime":
                raise ImportContractError()
            try:
                ZoneInfo(self.timestamp_zone)
            except (TypeError, ValueError, ZoneInfoNotFoundError):
                raise ImportContractError() from None

    def convert(self, value: Any) -> Any:
        """Return a bound parameter; invalid input is never truncated or defaulted."""
        try:
            if value is None:
                if self.nullable:
                    return None
                raise ImportValueError()
            if match := _VARCHAR.fullmatch(self.source_type):
                text = _text(value)
                if len(text) > int(match[1]):
                    raise ImportValueError()
                return text
            if self.source_type in ("int(11)", "bigint(20)"):
                bits = 32 if self.source_type == "int(11)" else 64
                if type(value) is not int or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                    raise ImportValueError()
                return value
            if self.source_type == "tinyint(1)":
                if type(value) not in (bool, int) or value not in (0, 1):
                    raise ImportValueError()
                return bool(value)
            if self.source_type == "double":
                if type(value) is not float or not math.isfinite(value):
                    raise ImportValueError()
                return value
            if self.source_type == "json":
                return Jsonb(_json_document(value), dumps=_raw_json_dump)
            if self.source_type == "datetime":
                return _timestamp(value, self.timestamp_zone)
        except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
            # Exceptions from decoders and encoders can contain source payloads.
            raise ImportValueError() from None
        raise ImportContractError()


def import_row_id(
    snapshot_id: UUID, source_table: str, chunk_sha256: str, row_index: int,
) -> UUID:
    """Identify an occurrence by its absolute row offset within the exported table.

    Reuse the same snapshot/chunks when resuming an import. Re-exporting or
    changing chunk boundaries describes a different import, not a retry. The
    offset is table-wide: two identical chunks must still have distinct IDs.
    """
    if (
        not isinstance(snapshot_id, UUID) or snapshot_id.int == 0
        or not isinstance(source_table, str) or not _TABLE.fullmatch(source_table)
        or not isinstance(chunk_sha256, str) or not _DIGEST.fullmatch(chunk_sha256)
        or type(row_index) is not int or not 0 <= row_index < 2 ** 63
    ):
        raise ImportContractError()
    address = json.dumps(
        ["nova-metadata-row-v1", source_table, chunk_sha256, row_index], separators=(",", ":"),
    )
    return uuid5(snapshot_id, address)
