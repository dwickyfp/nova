"""Catalog connectors: how Nova alters and drops each engine catalog type.

A connector turns a credential-free request into the engine statement for one
catalog type. Nova creates catalogs only from a registered connection
(:mod:`.sources` builds that statement); a connector here covers the changes
every catalog type still accepts afterwards:

* the native internal catalog, which is synced but never changed;
* foreign catalogs (one per registered source type, plus ``mysql`` and raw
  ``jdbc`` catalogs made before or outside Nova), altered only in their pool
  and cache properties;
* Iceberg and Hive catalogs made before sources replaced them, which can still
  be altered and dropped but no longer created.
"""

from __future__ import annotations

from typing import Protocol

from .errors import CatalogError, safe_catalog_name
from .schemas import ExternalCatalogAlter
from .sources import SOURCES, check_jdbc_properties

__all__ = [
    "INTERNAL_CATALOG",
    "CatalogConnector",
    "CatalogError",
    "connector_for",
    "quote_literal",
    "safe_catalog_name",
]

INTERNAL_CATALOG = "default_catalog"


def quote_literal(value: str) -> str:
    """Single-quote a SQL literal, escaping an embedded quote."""
    return "'" + value.replace("'", "''") + "'"


def _properties_clause(params: dict[str, str]) -> str:
    return ", ".join(
        f"{quote_literal(key)} = {quote_literal(value)}" for key, value in params.items()
    )


class CatalogConnector(Protocol):
    """Builds the engine statements for one catalog type."""

    catalog_type: str

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str: ...

    def build_drop_sql(self, name: str) -> str: ...


class LakehouseConnector:
    """An Iceberg or Hive catalog: alter its metastore or properties, or drop it."""

    def __init__(self, catalog_type: str) -> None:
        self.catalog_type = catalog_type

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str:
        name = safe_catalog_name(name)
        params: dict[str, str] = {}
        if body.metastore_uri:
            params["hive.metastore.uris"] = body.metastore_uri
        params.update(body.properties)
        if not params:
            raise CatalogError("Nothing to alter: no properties supplied")
        return f"ALTER CATALOG {name} SET ({_properties_clause(params)})"

    def build_drop_sql(self, name: str) -> str:
        return f"DROP CATALOG {safe_catalog_name(name)}"


class StarRocksNativeConnector:
    """The engine's internal catalog: synced like any other, never changed here."""

    catalog_type = "internal"

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str:
        raise CatalogError("The internal catalog cannot be altered")

    def build_drop_sql(self, name: str) -> str:
        raise CatalogError("The internal catalog cannot be dropped")


class JdbcConnector:
    """A foreign catalog: the engine's JDBC catalog over a source.

    Its driver, endpoint, and identity come from the connection; only the pool
    and metadata-cache properties can be altered here.
    """

    def __init__(self, catalog_type: str) -> None:
        self.catalog_type = catalog_type

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str:
        if body.metastore_uri:
            raise CatalogError("A JDBC catalog has no metastore; change its connection")
        params = check_jdbc_properties(body.properties)
        if not params:
            raise CatalogError("Nothing to alter: no properties supplied")
        return f"ALTER CATALOG {safe_catalog_name(name)} SET ({_properties_clause(params)})"

    def build_drop_sql(self, name: str) -> str:
        return f"DROP CATALOG {safe_catalog_name(name)}"


_ALL: list[CatalogConnector] = [
    StarRocksNativeConnector(),
    LakehouseConnector("iceberg"),
    LakehouseConnector("hive"),
    *(JdbcConnector(source_type) for source_type in SOURCES),
    # Foreign catalogs no source creates any more, and JDBC catalogs made in
    # raw SQL, which the sync records with the engine's type.
    JdbcConnector("mysql"),
    JdbcConnector("jdbc"),
]
_CONNECTORS: dict[str, CatalogConnector] = {c.catalog_type: c for c in _ALL}


def connector_for(catalog_type: str) -> CatalogConnector:
    """The connector for an engine or stored catalog type (case-insensitive)."""
    connector = _CONNECTORS.get((catalog_type or "").strip().lower())
    if connector is None:
        raise CatalogError(f"Unsupported catalog type: {catalog_type!r}")
    return connector
