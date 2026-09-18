"""Catalog metadata repository — NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS.

The engine catalog is the source of truth for the catalog itself; this table
mirrors the Nova-side metadata the engine does not retain: which storage
connection a catalog was built from and who created it.

Invariant (AGENTS.md §2, NOVA-62 constraint 1): **no credential-bearing column
exists here**. Only the storage *connection name* is stored; its secret stays in
``nova.yaml`` / env. ``properties_json`` holds non-secret catalog properties
only, and the service refuses to persist one that looks like a secret.
"""

from __future__ import annotations

import json
from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings

CONFIG_EXTERNAL_CATALOGS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS (
    id                 VARCHAR(64) NOT NULL,
    name               VARCHAR(256) NOT NULL,
    catalog_type       VARCHAR(32) NOT NULL,
    metastore_type     VARCHAR(32),
    metastore_uri      VARCHAR(1024),
    storage_connection VARCHAR(256),
    comment            VARCHAR(1024),
    properties_json    TEXT,
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by         VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class ExternalCatalogRepository:
    """Persist Nova-side catalog metadata. Never stores a secret."""

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user="root",
            password="",
            autocommit=True,
        )

    async def ensure_schema(self) -> None:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(CONFIG_EXTERNAL_CATALOGS_DDL)
        finally:
            conn.close()

    async def list_all(self) -> list[dict]:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, catalog_type, metastore_type, metastore_uri, "
                    "storage_connection, comment, properties_json, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS ORDER BY name"
                )
                return [self._row_to_dict(row) for row in await cur.fetchall()]
        finally:
            conn.close()

    async def get_by_name(self, name: str) -> dict | None:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, catalog_type, metastore_type, metastore_uri, "
                    "storage_connection, comment, properties_json, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS WHERE name = %s",
                    (name,),
                )
                row = await cur.fetchone()
                return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    async def upsert(
        self,
        *,
        name: str,
        catalog_type: str,
        metastore_type: str | None,
        metastore_uri: str | None,
        storage_connection: str | None,
        comment: str | None,
        properties: dict[str, str],
        username: str,
    ) -> None:
        """Insert or update the row for ``name`` (names are unique per engine)."""
        existing = await self.get_by_name(name)
        payload = json.dumps(properties or {})
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                if existing:
                    await cur.execute(
                        "UPDATE NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS SET "
                        "catalog_type = %s, metastore_type = %s, metastore_uri = %s, "
                        "storage_connection = %s, comment = %s, properties_json = %s "
                        "WHERE name = %s",
                        (
                            catalog_type,
                            metastore_type,
                            metastore_uri,
                            storage_connection,
                            comment,
                            payload,
                            name,
                        ),
                    )
                else:
                    await cur.execute(
                        "INSERT INTO NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS "
                        "(id, name, catalog_type, metastore_type, metastore_uri, "
                        "storage_connection, comment, properties_json, created_at, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)",
                        (
                            str(uuid4()),
                            name,
                            catalog_type,
                            metastore_type,
                            metastore_uri,
                            storage_connection,
                            comment,
                            payload,
                            username,
                        ),
                    )
        finally:
            conn.close()

    async def delete(self, name: str) -> bool:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_EXTERNAL_CATALOGS WHERE name = %s",
                    (name,),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row: dict) -> dict:
        data = dict(row)
        raw = data.pop("properties_json", None)
        props: dict[str, str] = {}
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    props = {str(k): str(v) for k, v in parsed.items()}
            except (json.JSONDecodeError, TypeError):
                props = {}
        data["properties"] = props
        return data


external_catalog_repo = ExternalCatalogRepository()
