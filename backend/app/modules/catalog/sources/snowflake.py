"""Snowflake as a Nova Catalog source.

A connection names one Snowflake database: ``account/DATABASE?warehouse=WH&role=ROLE``.
It authenticates with a user and a programmatic access token (PAT), entered as
the connection's password; the engine sends it as the JDBC ``password``, which
``SHOW CREATE CATALOG`` never prints.

The catalog's JDBC URI asks for JSON result sets (the BE JVM lacks the
``--add-opens`` Arrow needs) and pins the session ``TIMEZONE`` to Nova's, or
``TIMESTAMP_LTZ`` comes back in Snowflake's default zone.

Object kinds come from ``SHOW`` commands, which read metadata without a running
warehouse, so a sync never resumes one or spends credits.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, ClassVar
from urllib.parse import urlencode

from app.core.database import configured_timezone

from ..errors import CatalogError
from .base import (
    MATERIALIZED_VIEW,
    TABLE,
    VIEW,
    CatalogSource,
    Credentials,
    Endpoint,
    JdbcDriver,
    ObjectKind,
    ObjectKinds,
)

_DOMAIN = ".snowflakecomputing.com"


def _flag(row: dict[str, Any], key: str) -> bool:
    return str(row.get(key) or "").strip().upper() in {"Y", "YES", "TRUE"}


def table_kind(row: dict[str, Any]) -> ObjectKind:
    """The kind of one ``SHOW TABLES`` row, most specific first."""
    for key, label in (
        ("is_dynamic", "Dynamic table"),
        ("is_iceberg", "Iceberg table"),
        ("is_external", "External table"),
        ("is_hybrid", "Hybrid table"),
        ("is_event", "Event table"),
    ):
        if _flag(row, key):
            return ObjectKind(TABLE, label)
    kind = str(row.get("kind") or "").strip().upper()
    if kind == "TRANSIENT":
        return ObjectKind(TABLE, "Transient table")
    if kind == "TEMPORARY":
        return ObjectKind(TABLE, "Temporary table")
    return ObjectKind(TABLE, "Table")


def view_kind(row: dict[str, Any]) -> ObjectKind:
    if _flag(row, "is_materialized"):
        return ObjectKind(MATERIALIZED_VIEW, "Materialized view")
    if _flag(row, "is_secure"):
        return ObjectKind(VIEW, "Secure view")
    return ObjectKind(VIEW, "View")


#: ``SHOW`` commands and how their rows are classified. The kind-specific ones
#: come after the general ones so they win; one an account cannot run (an
#: edition without the feature) is skipped.
_SHOW: tuple[tuple[str, Any], ...] = (
    ("SHOW TABLES IN DATABASE", table_kind),
    ("SHOW VIEWS IN DATABASE", view_kind),
    ("SHOW MATERIALIZED VIEWS IN DATABASE",
     lambda _row: ObjectKind(MATERIALIZED_VIEW, "Materialized view")),
    ("SHOW EXTERNAL TABLES IN DATABASE", lambda _row: ObjectKind(TABLE, "External table")),
    ("SHOW DYNAMIC TABLES IN DATABASE", lambda _row: ObjectKind(TABLE, "Dynamic table")),
    ("SHOW ICEBERG TABLES IN DATABASE", lambda _row: ObjectKind(TABLE, "Iceberg table")),
    ("SHOW EVENT TABLES IN DATABASE", lambda _row: ObjectKind(TABLE, "Event table")),
)


def quote_identifier(name: str) -> str:
    """A Snowflake quoted identifier, exact case."""
    return '"' + name.replace('"', '""') + '"'


class SnowflakeSource(CatalogSource):
    connector_type = "snowflake"
    label = "Snowflake"
    # Keep the pin equal to docker/ranger/starrocks-fe.Dockerfile and
    # docker/starrocks-be.Dockerfile: the FE and every BE load this file.
    driver = JdbcDriver("snowflake-jdbc-3.28.0.jar", "net.snowflake.client.jdbc.SnowflakeDriver")
    jdbc_scheme = "snowflake"
    system_schemas: ClassVar[frozenset[str]] = frozenset({"INFORMATION_SCHEMA"})
    default_port = 443
    database_required = True
    endpoint_format = "account/DATABASE?warehouse=WAREHOUSE&role=ROLE"
    allowed_options: ClassVar[frozenset[str] | None] = frozenset({"warehouse", "role"})

    def normalize_host(self, host: str) -> str:
        """Accept the account identifier or its ``*.snowflakecomputing.com`` host."""
        if host.lower().endswith(_DOMAIN):
            host = host[: -len(_DOMAIN)]
        return host

    def parse_endpoint(self, value: str) -> Endpoint:
        account = (value or "").strip().partition("?")[0].partition("/")[0]
        if ":" in account:
            raise CatalogError("A Snowflake endpoint has no port: " + self.endpoint_format)
        endpoint = super().parse_endpoint(value)
        if "warehouse" not in endpoint.options:
            raise CatalogError(
                "A Snowflake endpoint names the warehouse queries run on: "
                + self.endpoint_format
            )
        return endpoint

    def jdbc_uri(self, endpoint: Endpoint) -> str:
        params = {"db": endpoint.database or ""}
        params.update(sorted(endpoint.options.items()))
        params["JDBC_QUERY_RESULT_FORMAT"] = "JSON"
        params["TIMEZONE"] = configured_timezone()
        return f"jdbc:snowflake://{endpoint.host}{_DOMAIN}/?" + urlencode(params)

    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        if not databases:
            return {}
        return await asyncio.to_thread(self._read_objects, endpoint, credentials, set(databases))

    def _read_objects(
        self, endpoint: Endpoint, credentials: Credentials, schemas: set[str]
    ) -> ObjectKinds:
        import snowflake.connector
        from snowflake.connector import DictCursor
        from snowflake.connector.errors import ProgrammingError

        kinds: ObjectKinds = {}
        connection = snowflake.connector.connect(
            account=endpoint.host,
            user=credentials.username,
            password=credentials.password,
            role=endpoint.options.get("role"),
            login_timeout=self.connect_timeout,
            network_timeout=self.connect_timeout * 4,
            application="Nova",
            client_session_keep_alive=False,
        )
        try:
            cursor = connection.cursor(DictCursor)
            database = quote_identifier(endpoint.database or "")
            for command, classify in _SHOW:
                try:
                    cursor.execute(f"{command} {database}")
                except ProgrammingError:
                    continue
                for row in cursor.fetchall():
                    schema, name = str(row.get("schema_name") or ""), str(row.get("name") or "")
                    if schema in schemas and name:
                        kinds[(schema, name)] = classify(row)
        finally:
            connection.close()
        return kinds
