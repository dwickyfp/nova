"""Migration Connector repository — source metadata + NOVA_SYSTEM persistence.

Two responsibilities, deliberately in one layer because both are I/O:

1. **Read source-cluster metadata** over a connection to the **registered
   source** (``app.modules.migration.source``). Enumeration follows the NOVA-84
   findings: tables/views from ``information_schema``, **materialized views from
   ``information_schema.materialized_views``** (not ``tables``), tasks/pipes from
   their own ``information_schema`` surfaces, functions from
   ``SHOW FULL FUNCTIONS``. DDL is read through ``SHOW CREATE``; MV DDL uses
   ``SHOW CREATE MATERIALIZED VIEW``.

   QA Finding 1 (High): every read takes the open source connection. There is no
   path that falls back to Nova's local system pool — an operator who registered
   a remote source must never be shown the local engine's objects.

2. **Persist registered source connections** in
   ``NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES``. The invariant is the same as
   ``CONFIG_EXTERNAL_CATALOGS``: **no credential-bearing value is stored**. The
   row carries the source *address* (host, port, username) and a *secret
   reference* for the password; the value stays in the secret store and is
   resolved at call time.

Nothing here executes a migration.
"""

from __future__ import annotations

from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings

CONFIG_MIGRATION_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES (
    id                 VARCHAR(64) NOT NULL,
    name               VARCHAR(256) NOT NULL,
    host               VARCHAR(256) NOT NULL,
    port               INT NOT NULL DEFAULT "9030",
    username           VARCHAR(128) NOT NULL DEFAULT "root",
    secret_ref         VARCHAR(1024),
    comment            VARCHAR(1024),
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by         VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

#: Columns added after the first revision shipped. ``CREATE TABLE IF NOT EXISTS``
#: cannot evolve an existing table, so they are added explicitly when absent
#: (StarRocks rejects ``ADD COLUMN IF NOT EXISTS``; idempotency is the caller's
#: job, same pattern as ``nova_system.migrate_task_orchestration_columns``).
SOURCE_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("host", "VARCHAR(256) NOT NULL DEFAULT ''"),
    ("port", 'INT NOT NULL DEFAULT "9030"'),
    ("username", 'VARCHAR(128) NOT NULL DEFAULT "root"'),
    ("secret_ref", "VARCHAR(1024)"),
)


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
                for column, column_type in SOURCE_COLUMN_MIGRATIONS:
                    if await self._column_exists(cur, column):
                        continue
                    await cur.execute(
                        f"ALTER TABLE NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
                        f"ADD COLUMN {column} {column_type}"
                    )
        finally:
            conn.close()

    @staticmethod
    async def _column_exists(cur: asyncmy.cursors.Cursor, column: str) -> bool:
        await cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' "
            "AND TABLE_NAME = 'CONFIG_MIGRATION_SOURCES' AND COLUMN_NAME = %s",
            (column,),
        )
        return bool(await cur.fetchall())

    # ── Source connection registry ──────────────────────────────

    _SELECT_COLUMNS = "id, name, host, port, username, secret_ref, comment, created_at, created_by"

    async def list_sources(self) -> list[dict]:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    f"SELECT {self._SELECT_COLUMNS} "
                    "FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES ORDER BY name"
                )
                return [dict(row) for row in await cur.fetchall()]
        finally:
            conn.close()

    async def get_source(self, name: str) -> dict | None:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    f"SELECT {self._SELECT_COLUMNS} "
                    "FROM NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES WHERE name = %s",
                    (name,),
                )
                row = await cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    async def create_source(
        self,
        *,
        name: str,
        host: str,
        port: int,
        username: str,
        secret_ref: str,
        comment: str,
        created_by: str,
    ) -> dict:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
                    "(id, name, host, port, username, secret_ref, comment, "
                    "created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)",
                    (
                        str(uuid4()),
                        name,
                        host,
                        port,
                        username,
                        secret_ref,
                        comment,
                        created_by,
                    ),
                )
        finally:
            conn.close()
        created = await self.get_source(name)
        assert created is not None
        return created

    # ── Enumeration (read-only, over the source connection) ─────

    @staticmethod
    async def _query(
        conn: asyncmy.Connection, sql: str, params: list | tuple | None = None
    ) -> list[tuple]:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            if not cur.description:
                return []
            return list(await cur.fetchall())

    async def list_databases(self, conn: asyncmy.Connection) -> list[str]:
        rows = await self._query(conn, "SHOW DATABASES")
        system = {"_statistics_", "information_schema", "sys"}
        return [row[0] for row in rows if row[0] not in system]

    async def list_tables(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """Base tables via ``information_schema.tables`` (TABLE_TYPE filter)."""
        rows = await self._query(
            conn,
            "SELECT TABLE_NAME FROM information_schema.tables "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME",
            (database,),
        )
        return [{"name": row[0], "kind": "table"} for row in rows]

    async def list_views(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """Views via ``information_schema.views``.

        StarRocks reports MVs as ``VIEW`` in ``information_schema.tables``, so
        views are enumerated from ``information_schema.views`` and MVs from
        ``materialized_views``; the two sets are disjoint by construction.
        """
        rows = await self._query(
            conn,
            "SELECT TABLE_NAME FROM information_schema.views "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
            (database,),
        )
        return [{"name": row[0], "kind": "view"} for row in rows]

    async def list_materialized_views(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """MVs via ``information_schema.materialized_views`` — never ``tables``."""
        rows = await self._query(
            conn,
            "SELECT TABLE_NAME, REFRESH_TYPE FROM "
            "information_schema.materialized_views "
            "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
            (database,),
        )
        return [
            {"name": row[0], "kind": "materialized_view", "refresh_type": row[1]} for row in rows
        ]

    async def list_functions(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """UDFs via ``SHOW FULL FUNCTIONS`` (best-effort; absent on some setups)."""
        try:
            rows = await self._query(conn, f"SHOW FULL FUNCTIONS FROM `{database}`")
        except Exception:
            return []
        functions = []
        for row in rows:
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

    async def list_tasks(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """TASKs via ``information_schema.tasks`` (no ``SHOW CREATE`` exists)."""
        try:
            rows = await self._query(
                conn,
                "SELECT TASK_NAME FROM information_schema.tasks "
                "WHERE DATABASE_NAME = %s ORDER BY TASK_NAME",
                (database,),
            )
        except Exception:
            return []
        return [{"name": row[0], "kind": "task"} for row in rows]

    async def list_pipes(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """PIPEs via ``information_schema.pipes`` (no ``SHOW CREATE`` exists)."""
        try:
            rows = await self._query(
                conn,
                "SELECT PIPE_NAME FROM information_schema.pipes "
                "WHERE DATABASE_NAME = %s ORDER BY PIPE_NAME",
                (database,),
            )
        except Exception:
            return []
        return [{"name": row[0], "kind": "pipe"} for row in rows]

    async def list_masking_policies(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """MASKING POLICYs via ``SHOW MASKING POLICIES`` when the surface exists.

        Returns ``[]`` when the engine does not expose the statement. Policies
        are always ``skipped`` regardless of how many are found; enumeration
        exists so the report is explicit rather than silently empty.
        """
        try:
            rows = await self._query(conn, "SHOW MASKING POLICIES")
        except Exception:
            return []
        policies = []
        for row in rows:
            # Column layout differs across versions; the name is first.
            name = row[0] if row else None
            if name:
                policies.append({"name": str(name), "kind": "masking_policy"})
        return policies

    async def list_row_access_policies(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """ROW ACCESS POLICYs via ``SHOW ROW ACCESS POLICIES`` when available."""
        try:
            rows = await self._query(conn, "SHOW ROW ACCESS POLICIES")
        except Exception:
            return []
        policies = []
        for row in rows:
            name = row[0] if row else None
            if name:
                policies.append({"name": str(name), "kind": "row_access_policy"})
        return policies

    # ── DDL / definitions (read-only, over the source connection) ─

    async def get_table_ddl(
        self, conn: asyncmy.Connection, database: str, table: str
    ) -> str | None:
        return await self._show_create(conn, f"SHOW CREATE TABLE `{database}`.`{table}`")

    async def get_view_ddl(self, conn: asyncmy.Connection, database: str, view: str) -> str | None:
        return await self._show_create(conn, f"SHOW CREATE VIEW `{database}`.`{view}`")

    async def get_materialized_view_ddl(
        self, conn: asyncmy.Connection, database: str, mv: str
    ) -> str | None:
        """MV DDL via ``SHOW CREATE MATERIALIZED VIEW``.

        This is the canonical async-MV surface (Ruling 3): it carries REFRESH,
        PARTITION BY and PROPERTIES, which ``SHOW CREATE VIEW`` drops. The raw
        string is returned unredacted here; the service applies the Nova-side
        sensitive-property filter before it reaches a response.
        """
        return await self._show_create(conn, f"SHOW CREATE MATERIALIZED VIEW `{database}`.`{mv}`")

    async def _show_create(self, conn: asyncmy.Connection, statement: str) -> str | None:
        try:
            rows = await self._query(conn, statement)
        except Exception:
            return None
        if not rows:
            return None
        row = rows[0]
        # SHOW CREATE returns (name, ddl) — the DDL is the second column.
        return row[1] if len(row) > 1 else None


migration_repo = MigrationRepository()
