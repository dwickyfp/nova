"""Nova Catalog sources: the external systems a foreign catalog can read.

:data:`SOURCES` is the registry. To add a source type, subclass
:class:`~.base.CatalogSource` and list an instance here.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import CatalogError
from .base import (
    OBJECT_GROUPS,
    CatalogSource,
    Credentials,
    Endpoint,
    JdbcDriver,
    ObjectKind,
    ObjectKinds,
    check_jdbc_properties,
)
from .postgres import PostgresSource
from .snowflake import SnowflakeSource
from .starrocks import StarRocksSource

__all__ = [
    "OBJECT_GROUPS",
    "NATIVE_GROUPS",
    "SOURCES",
    "CatalogSource",
    "SourceInfo",
    "Credentials",
    "Endpoint",
    "JdbcDriver",
    "ObjectKind",
    "ObjectKinds",
    "check_jdbc_properties",
    "describe_catalog_source",
    "parse_endpoint",
    "source_for",
    "validate_connector_type",
]

SOURCES: dict[str, CatalogSource] = {
    source.connector_type: source
    for source in (StarRocksSource(), PostgresSource(), SnowflakeSource())
}

#: Other spellings people write for a source type.
_ALIASES = {"postgresql": "postgres"}


def validate_connector_type(value: str) -> str:
    """The registered source type ``value`` names, else raise."""
    connector_type = (value or "").strip().lower()
    connector_type = _ALIASES.get(connector_type, connector_type)
    if connector_type not in SOURCES:
        raise CatalogError(
            f"Unsupported connection type {value!r}; expected one of "
            + ", ".join(sorted(SOURCES))
        )
    return connector_type


def source_for(connector_type: str) -> CatalogSource:
    return SOURCES[validate_connector_type(connector_type)]


def parse_endpoint(connector_type: str, value: str) -> Endpoint:
    return source_for(connector_type).parse_endpoint(value)


#: Explorer groups of Nova's own catalog: its data plus Nova's core objects.
NATIVE_GROUPS: tuple[str, ...] = (
    *OBJECT_GROUPS, "entities", "semantic_views", "feature_views",
    "functions", "pipes", "stages", "tasks",
)

#: Names of catalog types no source creates: made before sources, or in raw SQL.
_OTHER_LABELS = {
    "internal": "Nova", "iceberg": "Iceberg", "hive": "Hive", "mysql": "MySQL", "jdbc": "JDBC",
}


@dataclass(frozen=True)
class SourceInfo:
    """How the explorer presents one catalog."""

    source_type: str
    label: str
    object_groups: tuple[str, ...]
    system_schemas: frozenset[str] = frozenset()


def describe_catalog_source(catalog_type: str | None) -> SourceInfo:
    """Presentation of a catalog by its stored or engine type."""
    key = (catalog_type or "").strip().lower()
    source = SOURCES.get(_ALIASES.get(key, key))
    if source is not None:
        return SourceInfo(source.connector_type, source.label, source.object_groups,
                          source.system_schemas)
    if key == "internal":
        return SourceInfo("internal", _OTHER_LABELS[key], NATIVE_GROUPS)
    return SourceInfo(key or "external", _OTHER_LABELS.get(key, key.title() or "External"),
                      OBJECT_GROUPS)
