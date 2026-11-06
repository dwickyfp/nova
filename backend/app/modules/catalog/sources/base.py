"""The parent class of every Nova Catalog source.

A *source* is an external system a foreign catalog reads from. Each source type
is one :class:`CatalogSource` subclass that knows three things about its
system:

* how a connection endpoint is written and validated (:meth:`parse_endpoint`);
* the StarRocks JDBC catalog that reads it (:meth:`jdbc_uri`,
  :meth:`build_catalog_sql`), with the driver bundled in the FE and BE images;
* how to classify its relations (:meth:`read_objects`). The engine reports
  every JDBC relation as a plain table, so a source reads its own metadata to
  tell tables, views, and materialized views apart.

Adding a source type is one subclass plus its entry in
:data:`app.modules.catalog.sources.SOURCES`; the connection service, the
foreign-catalog DDL, the sync, and the explorer pick it up from there.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode

from app.common.sql_guard import is_credential_property

from ..errors import CatalogError, safe_catalog_name

#: Where the FE and BE images keep bundled JDBC drivers. StarRocks loads the
#: driver from ``driver_url`` on both: the FE to read metadata, every BE to scan.
#: A ``file://`` URL under this directory means no download at create time.
JDBC_DRIVER_DIR = "/opt/starrocks/drivers"

#: Explorer groups a source catalog shows. External catalogs are data
#: providers: Nova's own objects (stages, pipes, tasks, ...) live in Nova.
OBJECT_GROUPS: tuple[str, ...] = ("tables", "views", "materialized_views")

TABLE, VIEW, MATERIALIZED_VIEW = "table", "view", "materialized_view"

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

    @property
    def driver_url(self) -> str:
        return f"file://{JDBC_DRIVER_DIR}/{self.jar}"


@dataclass(frozen=True)
class Endpoint:
    """Where a connection points: no user, no password, ever."""

    host: str
    port: int
    database: str | None = None
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Credentials:
    """A resolved connection credential. Lives only for one engine or source call."""

    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class ObjectKind:
    """What a relation is in its own system.

    ``object_type`` picks the explorer group (``table``, ``view``,
    ``materialized_view``); ``label`` names the source's own kind, such as
    ``Dynamic table`` or ``Foreign table``.
    """

    object_type: str
    label: str


ObjectKinds = dict[tuple[str, str], ObjectKind]


def engine_literal(value: str) -> str:
    """A StarRocks string literal: the engine reads a backslash as an escape,
    so a password holding one must have it doubled to arrive unchanged."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def check_jdbc_properties(properties: dict[str, str]) -> dict[str, str]:
    """Refuse JDBC catalog properties that Nova owns or does not allow."""
    for key in properties:
        if is_credential_property(key) or not _JDBC_ALLOWED_PROPERTY.match(key):
            raise CatalogError(
                f"Property '{key}' cannot be set on a foreign catalog; the driver, "
                "endpoint, and identity come from the connection"
            )
    return dict(properties)


class CatalogSource(ABC):
    """One external system type. Subclass it to add a source."""

    #: Stored on the connection and the catalog row (``postgres``, ...).
    connector_type: ClassVar[str]
    #: Product name shown to people.
    label: ClassVar[str]
    driver: ClassVar[JdbcDriver]
    jdbc_scheme: ClassVar[str]
    default_port: ClassVar[int]
    #: The endpoint must name a database (``host:port/database``).
    database_required: ClassVar[bool] = False
    #: How an endpoint is written, for messages.
    endpoint_format: ClassVar[str] = "host[:port][/database]"
    #: Options an endpoint may carry; ``None`` accepts any safe option.
    allowed_options: ClassVar[frozenset[str] | None] = None
    object_groups: ClassVar[tuple[str, ...]] = OBJECT_GROUPS
    #: The source's own catalog schemas, never shown as databases.
    system_schemas: ClassVar[frozenset[str]] = frozenset()
    #: Seconds a metadata read may wait to connect.
    connect_timeout: ClassVar[int] = 15

    # ── Endpoint ─────────────────────────────────────────────────

    def parse_endpoint(self, value: str) -> Endpoint:
        """Parse ``host[:port][/database][?option=value&...]``.

        An endpoint is metadata, so anything that could carry a credential is
        refused: ``user@host``, a URL scheme, and a credential-named option.
        """
        text = (value or "").strip()
        if not text or len(text) > 1024:
            raise CatalogError(f"A connection endpoint is required ({self.endpoint_format})")
        if "@" in text or "://" in text or any(ch.isspace() for ch in text):
            raise CatalogError(
                f"A {self.label} endpoint is {self.endpoint_format}; credentials and URL "
                "schemes are not accepted"
            )
        text, _, query = text.partition("?")
        address, _, database = text.partition("/")
        host, _, port_text = address.partition(":")
        host = self.normalize_host(host)
        if not _HOST.match(host):
            raise CatalogError("The connection endpoint has an invalid host")
        try:
            port = int(port_text) if port_text else self.default_port
        except ValueError:
            raise CatalogError("The connection endpoint has an invalid port") from None
        if not 0 < port < 65536:
            raise CatalogError("The connection endpoint has an invalid port")
        if database and not _DATABASE.match(database):
            raise CatalogError("The connection endpoint has an invalid database name")
        if self.database_required and not database:
            raise CatalogError(
                f"A {self.label} endpoint names its database: {self.endpoint_format}"
            )
        options: dict[str, str] = {}
        for key, option in parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query)):
            if (
                not _OPTION_KEY.match(key)
                or not _OPTION_VALUE.match(option)
                or is_credential_property(key)
                or key.lower() in {"user", "username"}
                or (self.allowed_options is not None and key not in self.allowed_options)
            ):
                raise CatalogError(f"Connection option {key!r} is not accepted")
            options[key] = option
        return Endpoint(host, port, database or None, options)

    def normalize_host(self, host: str) -> str:
        """The host as stored; a subclass may accept an alternate spelling."""
        return host

    # ── Engine catalog ───────────────────────────────────────────

    def jdbc_uri(self, endpoint: Endpoint) -> str:
        uri = f"jdbc:{self.jdbc_scheme}://{endpoint.host}:{endpoint.port}"
        if endpoint.database:
            uri += f"/{endpoint.database}"
        if endpoint.options:
            uri += "?" + urlencode(sorted(endpoint.options.items()))
        return uri

    def engine_properties(self, endpoint: Endpoint, credentials: Credentials) -> dict[str, str]:
        """The engine catalog's properties. Carries the password."""
        return {
            "type": "jdbc",
            "user": credentials.username,
            "password": credentials.password,
            "jdbc_uri": self.jdbc_uri(endpoint),
            "driver_url": self.driver.driver_url,
            "driver_class": self.driver.driver_class,
        }

    def build_catalog_sql(
        self,
        name: str,
        endpoint: Endpoint,
        credentials: Credentials,
        *,
        comment: str | None = None,
        properties: dict[str, str] | None = None,
    ) -> str:
        """``CREATE EXTERNAL CATALOG`` for the engine. Carries the password."""
        params = self.engine_properties(endpoint, credentials)
        params.update(check_jdbc_properties(properties or {}))
        clause = f"COMMENT {engine_literal(comment)} " if comment else ""
        values = ", ".join(
            f"{engine_literal(key)} = {engine_literal(value)}" for key, value in params.items()
        )
        return f"CREATE EXTERNAL CATALOG {safe_catalog_name(name)} {clause}PROPERTIES ({values})"

    # ── Source metadata ──────────────────────────────────────────

    @abstractmethod
    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        """Kinds of the relations in ``databases``, keyed by ``(database, name)``.

        ``databases`` are the names the engine lists for the catalog (schemas
        for Postgres and Snowflake). A relation left out keeps the engine's
        classification.
        """
