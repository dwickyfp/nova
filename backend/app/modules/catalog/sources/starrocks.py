"""Another StarRocks cluster as a Nova Catalog source.

The built-in engine is ``default_catalog`` and is registered at bootstrap; this
source is for a *different* StarRocks cluster, read through the MySQL protocol
on its FE query port. The engine's JDBC catalog uses the MariaDB driver for it,
the same driver class the FE swaps in for any MySQL-protocol source.
"""

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


class StarRocksSource(CatalogSource):
    connector_type = "starrocks"
    label = "StarRocks"
    driver = JdbcDriver("mariadb-java-client-3.3.2.jar", "org.mariadb.jdbc.Driver")
    jdbc_scheme = "mariadb"
    system_schemas: ClassVar[frozenset[str]] = frozenset(
        {"_statistics_", "information_schema", "sys"}
    )
    default_port = 9030
    endpoint_format = "host[:port]"

    async def read_objects(
        self, endpoint: Endpoint, credentials: Credentials, databases: Sequence[str]
    ) -> ObjectKinds:
        import asyncmy

        if not databases:
            return {}
        placeholders = ", ".join(["%s"] * len(databases))
        connection = await asyncmy.connect(
            host=endpoint.host,
            port=endpoint.port,
            user=credentials.username,
            password=credentials.password,
            connect_timeout=self.connect_timeout,
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.tables "
                    f"WHERE TABLE_SCHEMA IN ({placeholders})",
                    list(databases),
                )
                tables = await cursor.fetchall()
                await cursor.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.materialized_views "
                    f"WHERE TABLE_SCHEMA IN ({placeholders})",
                    list(databases),
                )
                mvs = {(str(schema), str(name)) for schema, name in await cursor.fetchall()}
        finally:
            connection.close()
        kinds: ObjectKinds = {}
        for schema, name, table_type in tables:
            key = (str(schema), str(name))
            if key in mvs:
                kinds[key] = ObjectKind(MATERIALIZED_VIEW, "Materialized view")
            elif "VIEW" in str(table_type or "").upper():
                kinds[key] = ObjectKind(VIEW, "View")
            else:
                kinds[key] = ObjectKind(TABLE, "Table")
        return kinds
