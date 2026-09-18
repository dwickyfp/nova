"""Migration Connector repository — source metadata + NOVA_SYSTEM persistence.

Two responsibilities, deliberately in one layer because both are I/O:

1. **Read source-cluster metadata** through the existing ``db.execute_system``
   admin pool. Enumeration follows the NOVA-84 findings: tables/views from
   ``information_schema``, **materialized views from
   ``information_schema.materialized_views``** (not ``tables``), tasks/pipes from
   their own ``information_schema`` surfaces, functions from
   ``SHOW FULL FUNCTIONS``. DDL is read through ``SHOW CREATE``; MV DDL uses
   ``SHOW CREATE MATERIALIZED VIEW``.

2. **Persist registered source connections** in
   ``NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES``. The invariant is the same as
   ``CONFIG_EXTERNAL_CATALOGS``: **no credential-bearing column exists**. Only
   the storage *connection name* is stored; the secret stays in ``nova.yaml``/env
   and is resolved with ``resolve_storage_credentials``.

Nothing here executes a migration. Read-only plus an idempotent schema.
"""

from __future__ import annotations

from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings
from app.core.database import db

CONFIG_MIGRATION_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES (
    id                 VARCHAR(64) NOT NULL,
    name               VARCHAR(256) NOT NULL,
    storage_connection VARCHAR(256) NOT NULL,
    comment            VARCHAR(1024),
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by         VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class MigrationRepository:
    """Source metadata reads + source-connection persistence."""

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
                await cur.execute(CONFIG_MIGRATION_SOURCES_DDL)
        finally:
            conn.close()

    # ── Source connection registry ──────────────────────────────

    async def list_sources(self) -> list[dict]:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, storage_connection, comment, created_at, "
                    "created_by FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
                    "ORDER BY name"
                )
                return [dict(row) for row in await cur.fetchall()]
        finally:
            conn.close()

    async def get_source(self, name: str) -> dict | None:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, storage_connection, comment, created_at, "
                    "created_by FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
                    "WHERE name = %s",
                    (name,),
                )
                row = await cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    async def create_source(
        self, *, name: str, storage_connection: str, comment: str, username: str
    ) -> dict:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
                    "(id, name, storage_connection, comment, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, NOW(), %s)",
                    (str(uuid4()), name, storage_connection, comment, username),
                )
        finally:
            conn.close()
        created = await self.get_source(name)
        assert created is not None
        return created

    # ── Enumeration (read-only, source cluster) ─────────────────

    async def list_databases(self) -> list[str]:
        result = await db.execute_system("SHOW DATABASES")
        system = {"_statistics_", "information_schema", "sys"}
        return [row[0] for row in result["rows"] if row[0] not in system]

    async def list_tables(self, database: str) -> list[dict]:
        """Base tables via ``information_schema.tables`` (TABLE_TYPE filter)."""
        sql = (
            "SELECT TABLE_NAME FROM information_schema.tables "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME"
        )
        result = await db.execute_system(sql, [database])
        return [{"name": row[0], "kind": "table"} for row in result["rows"]]

    async def list_views(self, database: str) -> list[dict]:
        """Views via ``information_schema.views``.

        StarRocks reports MVs as ``VIEW`` in ``information_schema.tables``, so
        views are enumerated from ``information_schema.views`` and MVs from
        ``materialized_views``; the two sets are disjoint by construction.
        """
        sql = (
            "SELECT TABLE_NAME FROM information_schema.views "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
        )
        result = await db.execute_system(sql, [database])
        return [{"name": row[0], "kind": "view"} for row in result["rows"]]

    async def list_materialized_views(self, database: str) -> list[dict]:
        """MVs via ``information_schema.materialized_views`` — never ``tables``."""
        sql = (
            "SELECT TABLE_NAME, REFRESH_TYPE FROM "
            "information_schema.materialized_views "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
        )
        result = await db.execute_system(sql, [database])
        return [
            {"name": row[0], "kind": "materialized_view", "refresh_type": row[1]}
            for row in result["rows"]
        ]

    async def list_functions(self, database: str) -> list[dict]:
        """UDFs via ``SHOW FULL FUNCTIONS`` (best-effort; absent on some setups)."""
        try:
            result = await db.execute_system(f"SHOW FULL FUNCTIONS FROM `{database}`")
        except Exception:
            return []
        functions = []
        for row in result["rows"]:
            signature = row[0] or ""
            name = signature.split("(")[0] if signature else ""
            functions.append(
                {
                    "name": name,
                    "kind": "function",
                    "signature": signature,
                    "return_type": row[1] if len(row) > 1 else None,
                    "function_type": row[2] if len(row) > 2 else None,
                    "properties": row[4] if len(row) > 4 else None,
                }
            )
        return functions

    async def list_tasks(self, database: str) -> list[dict]:
        """TASKs via ``information_schema.tasks`` (no ``SHOW CREATE`` exists)."""
        try:
            result = await db.execute_system(
                "SELECT TASK_NAME FROM information_schema.tasks "
                "WHERE DATABASE_NAME = %s ORDER BY TASK_NAME",
                [database],
            )
        except Exception:
            return []
        return [{"name": row[0], "kind": "task"} for row in result["rows"]]

    async def list_pipes(self, database: str) -> list[dict]:
        """PIPEs via ``information_schema.pipes`` (no ``SHOW CREATE`` exists)."""
        try:
            result = await db.execute_system(
                "SELECT PIPE_NAME FROM information_schema.pipes "
                "WHERE DATABASE_NAME = %s ORDER BY PIPE_NAME",
                [database],
            )
        except Exception:
            return []
        return [{"name": row[0], "kind": "pipe"} for row in result["rows"]]

    async def list_masking_policies(self, database: str) -> list[dict]:
        """MASKING POLICYs via ``SHOW MASKING POLICIES`` when the surface exists.

        Returns ``[]`` when the engine does not expose the statement. Policies
        are always ``skipped`` regardless of how many are found; enumeration
        exists so the report is explicit rather than silently empty.
        """
        try:
            result = await db.execute_system("SHOW MASKING POLICIES")
        except Exception:
            return []
        policies = []
        for row in result["rows"]:
            # Column layout differs across versions; the name is first.
            name = row[0] if row else None
            if name:
                policies.append({"name": str(name), "kind": "masking_policy"})
        return policies

    async def list_row_access_policies(self, database: str) -> list[dict]:
        """ROW ACCESS POLICYs via ``SHOW ROW ACCESS POLICIES`` when available."""
        try:
            result = await db.execute_system("SHOW ROW ACCESS POLICIES")
        except Exception:
            return []
        policies = []
        for row in result["rows"]:
            name = row[0] if row else None
            if name:
                policies.append({"name": str(name), "kind": "row_access_policy"})
        return policies

    # ── DDL / definitions (read-only, source cluster) ───────────

    async def get_table_ddl(self, database: str, table: str) -> str | None:
        return await self._show_create(f"SHOW CREATE TABLE `{database}`.`{table}`")

    async def get_view_ddl(self, database: str, view: str) -> str | None:
        return await self._show_create(f"SHOW CREATE VIEW `{database}`.`{view}`")

    async def get_materialized_view_ddl(self, database: str, mv: str) -> str | None:
        """MV DDL via ``SHOW CREATE MATERIALIZED VIEW``.

        This is the canonical async-MV surface (Ruling 3): it carries REFRESH,
        PARTITION BY and PROPERTIES, which ``SHOW CREATE VIEW`` drops. The raw
        string is returned unredacted here; the service applies the Nova-side
        sensitive-property filter before it reaches a response.
        """
        return await self._show_create(f"SHOW CREATE MATERIALIZED VIEW `{database}`.`{mv}`")

    async def _show_create(self, statement: str) -> str | None:
        try:
            result = await db.execute_system(statement)
        except Exception:
            return None
        if not result["rows"]:
            return None
        row = result["rows"][0]
        # SHOW CREATE returns (name, ddl) — the DDL is the second column.
        return row[1] if len(row) > 1 else None


migration_repo = MigrationRepository()
