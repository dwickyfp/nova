"""PostgreSQL as a Nova Catalog source."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

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

#: ``pg_class.relkind`` → what Nova shows. Sequences, indexes, and composite
#: types are not relations a catalog exposes.
RELKINDS: dict[str, ObjectKind] = {
    "r": ObjectKind(TABLE, "Table"),
    "p": ObjectKind(TABLE, "Partitioned table"),
    "f": ObjectKind(TABLE, "Foreign table"),
    "v": ObjectKind(VIEW, "View"),
    "m": ObjectKind(MATERIALIZED_VIEW, "Materialized view"),
}

_RELATIONS_SQL = (
    "SELECT n.nspname, c.relname, c.relkind FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = ANY(%s) AND n.nspname = ANY(%s)"
)


class PostgresSource(CatalogSource):
    connector_type = "postgres"
    label = "PostgreSQL"
    # The driver the pinned upstream FE image ships, copied into the BE image.
    driver = JdbcDriver("postgresql-42.7.12.jar", "org.postgresql.Driver")
    jdbc_scheme = "postgresql"
    system_schemas: ClassVar[frozenset[str]] = frozenset({"pg_catalog", "information_schema"})
    default_port = 5432
    database_required = True
    endpoint_format = "host[:port]/database"
    #: libpq/JDBC options that only shape the transport.
    allowed_options: ClassVar[frozenset[str] | None] = frozenset(
        {"sslmode", "connectTimeout", "socketTimeout", "ApplicationName"}
    )

    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        import psycopg

        if not databases:
            return {}
        async with await psycopg.AsyncConnection.connect(
            host=endpoint.host,
            port=endpoint.port,
            dbname=endpoint.database,
            user=credentials.username,
            password=credentials.password,
            sslmode=endpoint.options.get("sslmode", "prefer"),
            connect_timeout=self.connect_timeout,
            application_name="nova-catalog",
        ) as connection:
            # A metadata read only: never leave a transaction open on the source.
            await connection.set_read_only(True)
            cursor = await connection.execute(
                _RELATIONS_SQL, [list(RELKINDS), list(databases)]
            )
            rows = await cursor.fetchall()
        return {
            (str(schema), str(name)): RELKINDS[str(kind)]
            for schema, name, kind in rows
            if str(kind) in RELKINDS
        }
