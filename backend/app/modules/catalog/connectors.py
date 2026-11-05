"""Catalog connectors: how Nova creates, alters, and drops each catalog type.

A connector turns a credential-free request into the engine statement for one
catalog type. Storage secrets are resolved here, at statement-build time, from
the named storage connection (``resolve_storage_credentials``, the same resolver
``@stage`` uses), so a request, a response, and a catalog store row never hold
one. A built statement carries real credentials and goes only to the engine.

Phase 1 ships Iceberg and Hive (the GA types) plus the native internal catalog,
which is synced but never created. Phase 2 adds Postgres and MySQL as StarRocks
JDBC catalogs built from a registered connection (:class:`JdbcConnector`): the
connection's credentials are resolved after authorization and exist only in
the statement sent to the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlencode

from app.common.sql_guard import is_credential_property
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


def _engine_literal(value: str) -> str:
    """A StarRocks string literal: the engine reads a backslash as an escape,
    so a password holding one must have it doubled to arrive unchanged."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


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


# ---------------------------------------------------------------------------
# JDBC sources (phase 2)
# ---------------------------------------------------------------------------

#: Where the FE and BE images keep bundled JDBC drivers. StarRocks loads the
#: driver from ``driver_url`` on both: the FE to read metadata, every BE to scan.
#: A ``file://`` URL under this directory means no download at create time.
JDBC_DRIVER_DIR = "/opt/starrocks/drivers"

_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,252}[A-Za-z0-9])?$")
_DATABASE = re.compile(r"^[A-Za-z0-9_$][A-Za-z0-9_$.\-]{0,127}$")
_OPTION_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")
_OPTION_VALUE = re.compile(r"^[A-Za-z0-9_.:/\-]{0,256}$")

#: Engine catalog properties a caller may add to a JDBC catalog. Everything
#: that picks the driver, the endpoint, or the identity comes from Nova:
#: accepting ``driver_url`` would let a caller make every FE and BE load an
#: arbitrary jar.
_JDBC_ALLOWED_PROPERTY = re.compile(
    r"^jdbc_(meta_\w+|row_count_cache_\w+|connection_pool_size|minimum_idle_connections"
    r"|connection_idle_timeout_ms)$"
)


@dataclass(frozen=True)
class JdbcDriver:
    """A JDBC driver bundled in the FE and BE images."""

    jar: str
    driver_class: str
    scheme: str
    default_port: int
    database_required: bool

    @property
    def driver_url(self) -> str:
        return f"file://{JDBC_DRIVER_DIR}/{self.jar}"


#: The engine selects its schema resolver from the driver class name. MySQL uses
#: the MariaDB driver: 4.1.4 swaps a ``mysql`` driver class for it anyway
#: (``JDBCMetadata.getDriverName``), and naming it directly keeps the FE and the
#: BE scanner on the same class and jar.
JDBC_DRIVERS: dict[str, JdbcDriver] = {
    "postgres": JdbcDriver(
        "postgresql-42.7.12.jar", "org.postgresql.Driver", "postgresql", 5432, True
    ),
    "mysql": JdbcDriver(
        "mariadb-java-client-3.3.2.jar", "org.mariadb.jdbc.Driver", "mariadb", 3306, False
    ),
}


@dataclass(frozen=True)
class Endpoint:
    """Where a connection points: no user, no password, ever."""

    host: str
    port: int
    database: str | None = None
    options: dict[str, str] = field(default_factory=dict)

    def jdbc_uri(self, driver: JdbcDriver) -> str:
        uri = f"jdbc:{driver.scheme}://{self.host}:{self.port}"
        if self.database:
            uri += f"/{self.database}"
        if self.options:
            uri += "?" + urlencode(sorted(self.options.items()))
        return uri


def validate_connector_type(value: str) -> str:
    """The connector type for a connection (``postgres`` or ``mysql``)."""
    connector_type = (value or "").strip().lower()
    if connector_type == "postgresql":
        connector_type = "postgres"
    if connector_type not in JDBC_DRIVERS:
        raise CatalogError(
            f"Unsupported connection type {value!r}; expected one of "
            + ", ".join(sorted(JDBC_DRIVERS))
        )
    return connector_type


def parse_endpoint(connector_type: str, value: str) -> Endpoint:
    """Parse ``host[:port][/database][?option=value&...]``.

    An endpoint is metadata, so anything that could carry a credential is
    refused: ``user@host``, a URL scheme, and a credential-named option.
    """
    driver = JDBC_DRIVERS[validate_connector_type(connector_type)]
    text = (value or "").strip()
    if not text or len(text) > 1024:
        raise CatalogError("A connection endpoint is required (host[:port][/database])")
    if "@" in text or "://" in text or any(ch.isspace() for ch in text):
        raise CatalogError(
            "A connection endpoint is host[:port][/database]; credentials and URL "
            "schemes are not accepted"
        )
    text, _, query = text.partition("?")
    address, _, database = text.partition("/")
    host, _, port_text = address.partition(":")
    if not _HOST.match(host):
        raise CatalogError("The connection endpoint has an invalid host")
    try:
        port = int(port_text) if port_text else driver.default_port
    except ValueError:
        raise CatalogError("The connection endpoint has an invalid port") from None
    if not 0 < port < 65536:
        raise CatalogError("The connection endpoint has an invalid port")
    if database and not _DATABASE.match(database):
        raise CatalogError("The connection endpoint has an invalid database name")
    if driver.database_required and not database:
        raise CatalogError("A Postgres endpoint names its database: host:5432/database")
    options: dict[str, str] = {}
    for key, option in parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query)):
        if (
            not _OPTION_KEY.match(key)
            or not _OPTION_VALUE.match(option)
            or is_credential_property(key)
            or key.lower() in {"user", "username"}
        ):
            raise CatalogError(f"Connection option {key!r} is not accepted")
        options[key] = option
    return Endpoint(host, port, database or None, options)


def check_jdbc_properties(properties: dict[str, str]) -> dict[str, str]:
    """Refuse JDBC catalog properties that Nova owns or does not allow."""
    for key in properties:
        if is_credential_property(key) or not _JDBC_ALLOWED_PROPERTY.match(key):
            raise CatalogError(
                f"Property '{key}' cannot be set on a foreign catalog; the driver, "
                "endpoint, and identity come from the connection"
            )
    return dict(properties)


class JdbcConnector:
    """A Postgres or MySQL source as a StarRocks JDBC catalog.

    Created only from a registered connection (:meth:`build_foreign_catalog_sql`),
    never from a free-form request, so the driver, endpoint, and identity always
    come from Nova.
    """

    creatable = False

    def __init__(self, catalog_type: str) -> None:
        self.catalog_type = catalog_type

    def build_foreign_catalog_sql(
        self,
        name: str,
        *,
        connector_type: str,
        endpoint: Endpoint,
        username: str,
        password: str,
        comment: str | None = None,
        properties: dict[str, str] | None = None,
    ) -> str:
        """``CREATE EXTERNAL CATALOG`` for the engine. Carries the password."""
        driver = JDBC_DRIVERS[validate_connector_type(connector_type)]
        params: dict[str, str] = {
            "type": "jdbc",
            "user": username,
            "password": password,
            "jdbc_uri": endpoint.jdbc_uri(driver),
            "driver_url": driver.driver_url,
            "driver_class": driver.driver_class,
        }
        params.update(check_jdbc_properties(properties or {}))
        clause = f"COMMENT {_engine_literal(comment)} " if comment else ""
        values = ", ".join(
            f"{_engine_literal(key)} = {_engine_literal(value)}" for key, value in params.items()
        )
        return f"CREATE EXTERNAL CATALOG {safe_catalog_name(name)} {clause}PROPERTIES ({values})"

    def build_create_sql(self, body: ExternalCatalogCreate) -> str:
        raise CatalogError("Create a JDBC catalog from a connection: CREATE FOREIGN CATALOG")

    def build_alter_sql(self, name: str, body: ExternalCatalogAlter) -> str:
        if body.metastore_uri:
            raise CatalogError("A JDBC catalog has no metastore; change its connection")
        params = check_jdbc_properties(body.properties)
        if not params:
            raise CatalogError("Nothing to alter: no properties supplied")
        return f"ALTER CATALOG {safe_catalog_name(name)} SET ({_properties_clause(params)})"

    def build_drop_sql(self, name: str) -> str:
        return f"DROP CATALOG {safe_catalog_name(name)}"


_CONNECTORS: dict[str, CatalogConnector] = {
    connector.catalog_type: connector
    for connector in (
        IcebergConnector(),
        HiveConnector(),
        StarRocksNativeConnector(),
        JdbcConnector("postgres"),
        JdbcConnector("mysql"),
        # A JDBC catalog made in raw SQL is recorded with the engine's type.
        JdbcConnector("jdbc"),
    )
}


def connector_for(catalog_type: str) -> CatalogConnector:
    """The connector for an engine or request catalog type (case-insensitive)."""
    connector = _CONNECTORS.get((catalog_type or "").strip().lower())
    if connector is None:
        raise CatalogError(f"Unsupported catalog type: {catalog_type!r}")
    return connector
