"""Typed row images for the change journal, with exact round trips.

A journal row stores a source row's before/after image as JSON. Every value is
encoded by its column type so that reading it back yields the same Python value
(``Decimal`` keeps its scale, ``DATETIME`` its microseconds) and rendering it as
SQL yields a literal the engine casts to the original type. Types that cannot
round-trip exactly are refused when a source is registered, never stringified.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.streams.schemas import StreamError

_INTEGER = {"tinyint", "smallint", "int", "integer", "bigint", "largeint"}
_FLOAT = {"float", "double"}
_TEXT = {"char", "varchar", "string"}
_TYPE = re.compile(r"^([a-z]+)(\(\s*\d+\s*(,\s*\d+\s*)?\))?$")


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    sql_type: str  # the engine's COLUMN_TYPE, e.g. ``decimal(10,2)``

    @property
    def base(self) -> str:
        return column_base(self.sql_type)


def column_base(sql_type: str) -> str:
    match = _TYPE.match(sql_type.strip().lower())
    if not match:
        raise StreamError("STREAM_SCHEMA_UNSUPPORTED", f"Column type {sql_type!r} is not supported")
    base = match.group(1)
    if base.startswith("decimal"):
        return "decimal"
    return base


def supported(sql_type: str) -> bool:
    try:
        base = column_base(sql_type)
    except StreamError:
        return False
    return base in _INTEGER | _FLOAT | _TEXT | {"decimal", "boolean", "date", "datetime"}


def encode(column: Column, value: Any) -> Any:
    """A JSON-safe encoding that decodes back to an equal value of the same type."""
    if value is None:
        return None
    base = column.base
    if base in _INTEGER:
        if isinstance(value, bool):
            return int(value)
        return str(int(value)) if base == "largeint" else int(value)
    if base == "boolean":
        return bool(value)
    if base in _FLOAT:
        number = float(value)
        if not math.isfinite(number):
            raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Non-finite floats cannot be captured")
        return number
    if base == "decimal":
        return str(Decimal(str(value)) if not isinstance(value, Decimal) else value)
    if base == "date":
        if isinstance(value, dt.datetime):
            value = value.date()
        return value.isoformat() if isinstance(value, dt.date) else str(value)
    if base == "datetime":
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else str(value)
    if base in _TEXT:
        if isinstance(value, bytes):
            raise StreamError("STREAM_SCHEMA_UNSUPPORTED", "Binary text values are not supported")
        return str(value)
    raise StreamError(
        "STREAM_SCHEMA_UNSUPPORTED", f"Column type {column.sql_type!r} is not supported"
    )


def decode(column: Column, value: Any) -> Any:
    if value is None:
        return None
    base = column.base
    try:
        if base in _INTEGER:
            return int(value)
        if base == "boolean":
            return bool(value)
        if base in _FLOAT:
            return float(value)
        if base == "decimal":
            return Decimal(str(value))
        if base == "date":
            return dt.date.fromisoformat(str(value))
        if base == "datetime":
            return dt.datetime.fromisoformat(str(value))
        if base in _TEXT:
            return str(value)
    except (ValueError, InvalidOperation) as exc:
        raise StreamError("STREAM_JOURNAL_CORRUPT", "A journal value does not decode") from exc
    raise StreamError(
        "STREAM_SCHEMA_UNSUPPORTED", f"Column type {column.sql_type!r} is not supported"
    )


def encode_image(columns: tuple[Column, ...], row: dict[str, Any] | None) -> str | None:
    if row is None:
        return None
    return json.dumps(
        {column.name: encode(column, row.get(column.name)) for column in columns},
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_image(columns: tuple[Column, ...], text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise StreamError("STREAM_JOURNAL_CORRUPT", "A journal image is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {column.name for column in columns}:
        raise StreamError(
            "STREAM_SCHEMA_REVIEW_REQUIRED", "A journal image does not match the source columns"
        )
    return {column.name: decode(column, raw[column.name]) for column in columns}


def key_hash(columns: tuple[Column, ...], keys: tuple[str, ...], row: dict[str, Any]) -> str:
    by_name = {column.name: column for column in columns}
    payload = [encode(by_name[name], row.get(name)) for name in keys]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def literal(column: Column, value: Any) -> str:
    """``CAST(<literal> AS <type>)``: typed, injection-safe engine SQL."""
    sql_type = column.sql_type.strip().lower()
    if value is None:
        return f"CAST(NULL AS {sql_type})"
    encoded = encode(column, value)
    if column.base == "boolean":
        return "TRUE" if encoded else "FALSE"
    if column.base in _INTEGER and not isinstance(encoded, str):
        return f"CAST({int(encoded)} AS {sql_type})"
    if column.base in _FLOAT:
        return f"CAST({float(encoded)!r} AS {sql_type})"
    return f"CAST({_quote(str(encoded))} AS {sql_type})"


def string_literal(text: str | None) -> str:
    return "CAST(NULL AS VARCHAR)" if text is None else _quote(text)


def quote_identifier(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"
