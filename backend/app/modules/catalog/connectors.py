"""Catalog connectors: how Nova creates, alters, and drops each catalog type.

A connector turns a credential-free request into the engine statement for one
catalog type. Storage secrets are resolved here, at statement-build time, from
the named storage connection (``resolve_storage_credentials``, the same resolver
``@stage`` uses), so a request, a response, and a catalog store row never hold
one. A built statement carries real credentials and goes only to the engine.

Phase 1 ships Iceberg and Hive (the GA types) plus the native internal catalog,
which is synced but never created. JDBC sources join through the same
``connector_for`` registry in a later phase.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.core.config import get_storage_connection, settings, to_docker_endpoint
from app.modules.query.dialect.injector import resolve_storage_credentials

from .schemas import CatalogType, ExternalCatalogAlter, ExternalCatalogCreate, MetastoreType

#: Catalog identifiers are interpolated into engine statements on both the
#: caller's connection and the system pool, so they are validated at every entry
#: point: a URL path segment is not trusted because a create body was validated.
_CATALOG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INTERNAL_CATALOG = "default_catalog"


class CatalogError(ValueError):
    """A catalog operation Nova refused before or after the engine."""


def safe_catalog_name(name: str) -> str:
    """Return ``name`` if it is a bare catalog identifier, else raise."""
    if not _CATALOG_NAME.match(name or ""):
        raise CatalogError(f"Invalid catalog name: {name!r}")
    return name


def quote_literal(value: str) -> str:
    """Single-quote a SQL literal, escaping an embedded quote."""
    return "'" + value.replace("'", "''") + "'"


def _properties_clause(params: dict[str, str]) -> str:
    return ", ".join(
        f"{quote_literal(key)} = {quote_literal(value)}" for key, value in params.items()
    )


def build_storage_credential_params(storage_connection: str | None = None) -> dict[str, str]:
    """``aws.s3.*`` properties for a catalog, resolved from the storage connection.

    Returns an empty dict when the connection has no credentials, so catalog
    creation fails loudly at the engine instead of silently authenticating with
    a well-known default.
    """
    access_key, secret_key = resolve_storage_credentials(storage_connection)
    if not access_key or not secret_key:
        return {}

    connection = get_storage_connection(storage_connection or "")
    params = {
        "aws.s3.access_key": access_key,
        "aws.s3.secret_key": secret_key,
    }
    # StarRocks runs inside Docker and must reach MinIO by its internal name.
    endpoint = to_docker_endpoint(connection.endpoint or settings.S3_ENDPOINT)
    if endpoint:
        params["aws.s3.endpoint"] = endpoint
        params["aws.s3.enable_path_style_access"] = "true"
        if not endpoint.startswith("https"):
            params["aws.s3.enable_ssl"] = "false"
    if connection.region:
        params["aws.s3.region"] = connection.region
    return params


class CatalogConnector(Protocol):
    """Builds the engine statements for one catalog type."""

    catalog_type: str
    creatable: bool

    def build_create_sql(self, body: ExternalCatalogCreate) -> str: ...

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str: ...

    def build_drop_sql(self, name: str) -> str: ...


class _ExternalConnector:
    """Shared ``CREATE EXTERNAL CATALOG`` shape for metastore-backed types."""

    catalog_type = ""
    creatable = True

    def _metastore_params(self, body: ExternalCatalogCreate) -> dict[str, str]:
        raise NotImplementedError

    def build_create_sql(self, body: ExternalCatalogCreate) -> str:
        name = safe_catalog_name(body.name)
        params: dict[str, str] = {"type": body.type.value}
        params.update(self._metastore_params(body))
        params.update(body.properties)
        params.update(build_storage_credential_params(body.storage_connection))
        comment = f"COMMENT {quote_literal(body.comment)} " if body.comment else ""
        return f"CREATE EXTERNAL CATALOG {name} {comment}PROPERTIES ({_properties_clause(params)})"

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


class IcebergConnector(_ExternalConnector):
    catalog_type = CatalogType.ICEBERG.value

    def _metastore_params(self, body: ExternalCatalogCreate) -> dict[str, str]:
        # 4.1.4 requires the metastore selector and accepts ``hive``/``rest``
        # (not the doc's ``hms`` alias); REST additionally needs a URI.
        if body.metastore_type is MetastoreType.REST:
            return {"iceberg.catalog.type": "rest", "iceberg.catalog.uri": body.metastore_uri}
        return {"iceberg.catalog.type": "hive", "hive.metastore.uris": body.metastore_uri}


class HiveConnector(_ExternalConnector):
    catalog_type = CatalogType.HIVE.value

    def _metastore_params(self, body: ExternalCatalogCreate) -> dict[str, str]:
        return {"hive.metastore.uris": body.metastore_uri}


class StarRocksNativeConnector:
    """The engine's internal catalog: synced like any other, never created here."""

    catalog_type = "internal"
    creatable = False

    def build_create_sql(self, body: ExternalCatalogCreate) -> str:
        raise CatalogError("The internal catalog cannot be created")

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str:
        raise CatalogError("The internal catalog cannot be altered")

    def build_drop_sql(self, name: str) -> str:
        raise CatalogError("The internal catalog cannot be dropped")


_CONNECTORS: dict[str, CatalogConnector] = {
    connector.catalog_type: connector
    for connector in (IcebergConnector(), HiveConnector(), StarRocksNativeConnector())
}


def connector_for(catalog_type: str) -> CatalogConnector:
    """The connector for an engine or request catalog type (case-insensitive)."""
    connector = _CONNECTORS.get((catalog_type or "").strip().lower())
    if connector is None:
        raise CatalogError(f"Unsupported catalog type: {catalog_type!r}")
    return connector
