"""Feature profiling over Arrow schemas and bounded column statistics."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pyarrow as pa


@dataclass(frozen=True)
class FeatureProfile:
    name: str
    kind: str
    nullable: bool
    unique_values: int | None = None


def profile_table(table: pa.Table, columns: list[str]) -> list[FeatureProfile]:
    profiles: list[FeatureProfile] = []
    for name in columns:
        field = table.schema.field(name)
        data_type = field.type
        if pa.types.is_boolean(data_type):
            kind = "boolean"
        elif (
            pa.types.is_integer(data_type)
            or pa.types.is_floating(data_type)
            or pa.types.is_decimal(data_type)
        ):
            kind = "numeric"
        elif pa.types.is_date(data_type) or pa.types.is_timestamp(data_type):
            kind = "datetime"
        elif pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
            kind = "categorical"
        else:
            kind = "unsupported"
        unique = None
        if kind in {"categorical", "boolean"}:
            unique = len(table.column(name).unique())
        profiles.append(FeatureProfile(name, kind, field.nullable, unique))
    return profiles


def serialize_profiles(profiles: list[FeatureProfile]) -> list[dict]:
    return [asdict(profile) for profile in profiles]
