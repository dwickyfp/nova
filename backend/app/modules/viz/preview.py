"""Bounded, lossless preview values; rows never enter document persistence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.core.exceptions import NovaException
from app.core.timezones import resolve_timezone

from .contracts.common import (
    BooleanValue,
    DateValue,
    DecimalValue,
    FloatValue,
    IntegerValue,
    Scalar,
    StringValue,
    TimestampValue,
)
from .contracts.datasets import DatasetField

MAX_PREVIEW_BYTES = 1_048_576


def preview_value(field: DatasetField, value: object) -> Scalar | None:
    if value is None:
        if field.nullable:
            return None
        raise ValueError("Unexpected NULL in preview")
    kind = field.logical_type
    if kind == "string" and type(value) is str:
        return StringValue(value=value)
    if kind == "integer" and type(value) is int:
        return IntegerValue(value=str(value))
    if kind == "decimal" and isinstance(value, Decimal) and value.is_finite():
        return DecimalValue(value=format(value, "f"))
    if kind == "float" and type(value) is float:
        return FloatValue(value=value)
    if kind == "boolean" and (type(value) is bool or type(value) is int and value in (0, 1)):
        return BooleanValue(value=bool(value))
    if kind == "date" and type(value) is date:
        return DateValue(value=value.isoformat())
    if kind == "timestamp" and isinstance(value, datetime) and field.timezone:
        zone = resolve_timezone(field.timezone)
        if value.tzinfo is None:
            first, second = value.replace(tzinfo=zone), value.replace(tzinfo=zone, fold=1)
            # DATETIME carries no offset. Guessing one at a DST overlap or gap
            # would silently change the instant used by filters and charts.
            if first.utcoffset() != second.utcoffset() or (
                first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != value
            ):
                raise ValueError("Ambiguous local timestamp")
            value = first
        return TimestampValue(value=value.isoformat())
    raise ValueError("Preview value does not match its declared type")


class PreviewRows:
    fetch_batch_rows = 1

    def __init__(self, fields: tuple[DatasetField, ...], limit: int) -> None:
        self.fields = fields
        self.limit = limit
        self.values: list[tuple[Scalar | None, ...]] = []
        self.has_more = False
        self.count = 0
        self.bytes = sum(len(field.model_dump_json().encode()) for field in fields) + 1024
        if self.bytes > MAX_PREVIEW_BYTES:
            raise NovaException("Preview schema exceeds one MiB", status_code=413)
        self.begun = False

    async def begin(self, columns: list[str], column_types: tuple[str, ...]) -> None:
        if self.begun or columns != [field.source_column for field in self.fields]:
            raise NovaException("SCHEMA_STALE: inspect the source again", status_code=409)
        self.begun = True

    async def rows(self, rows: list[list]) -> None:
        for row in rows:
            self.count += 1
            if not self.begun or len(row) != len(self.fields) or self.count > self.limit + 1:
                raise NovaException("Invalid preview result", status_code=502)
            if self.count > self.limit:
                self.has_more = True
                continue
            try:
                values = tuple(
                    preview_value(field, value)
                    for field, value in zip(self.fields, row, strict=True)
                )
                self.bytes += 2 + sum(
                    5 if value is None else len(value.model_dump_json().encode()) + 1
                    for value in values
                )
            except (ValueError, KeyError, OverflowError):
                raise NovaException(
                    "Preview contains an unsupported value", status_code=422
                ) from None
            if self.bytes > MAX_PREVIEW_BYTES:
                raise NovaException(
                    "Preview exceeds one MiB; reduce the page size", status_code=413
                )
            self.values.append(values)
