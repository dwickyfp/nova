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
from asyncmy.errors import ProgrammingError

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

CONFIG_MIGRATION_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MIGRATION_JOBS (
    id                       VARCHAR(64) NOT NULL,
    operation                VARCHAR(32) NOT NULL,
    actor                    VARCHAR(128) NOT NULL,
    session_fingerprint      VARCHAR(64) NOT NULL DEFAULT "",
    active_role              VARCHAR(128),
    security_context_version INT NOT NULL DEFAULT "1",
    source_name              VARCHAR(256) NOT NULL,
    request_json             STRING NOT NULL,
    status                   VARCHAR(16) NOT NULL,
    current_database         VARCHAR(256),
    result_json              STRING,
    error_code               VARCHAR(64),
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at               DATETIME,
    heartbeat_at             DATETIME,
    finished_at              DATETIME
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

JOB_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("session_fingerprint", 'VARCHAR(64) NOT NULL DEFAULT ""'),
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
                await cur.execute(CONFIG_MIGRATION_JOBS_DDL)
                for column, column_type in SOURCE_COLUMN_MIGRATIONS:
                    if await self._column_exists(cur, column):
                        continue
                    await cur.execute(
                        f"ALTER TABLE NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES "
                        f"ADD COLUMN {column} {column_type}"
                    )
                for column, column_type in JOB_COLUMN_MIGRATIONS:
                    await cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.columns "
                        "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' "
                        "AND TABLE_NAME = 'CONFIG_MIGRATION_JOBS' AND COLUMN_NAME = %s",
                        (column,),
                    )
                    if await cur.fetchall():
                        continue
                    try:
                        await cur.execute(
                            f"ALTER TABLE NOVA_SYSTEM.CONFIG_MIGRATION_JOBS "
                            f"ADD COLUMN {column} {column_type}"
                        )
                    except ProgrammingError:
                        await cur.execute(
                            "SELECT COLUMN_NAME FROM information_schema.columns "
                            "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' "
                            "AND TABLE_NAME = 'CONFIG_MIGRATION_JOBS' AND COLUMN_NAME = %s",
                            (column,),
                        )
                        if not await cur.fetchall():
                            raise
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

    async def target_replication_num(self) -> int | None:
        """The local engine's safe replication factor, or ``None`` if unknown.

        A table copied from a source may carry a replication factor the target
        cannot satisfy (a multi-BE source into a single-BE target). This reads the
        number of alive backends on the **local** engine — the migration target in
        v1 — so the planner can clamp ``replication_num`` instead of dropping it
        and letting an unsatisfiable default apply. Returns ``None`` when the
        engine cannot be read, in which case the property is dropped as before.
        """
        try:
            conn = await self._connect()
        except Exception:
            return None
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute("SHOW BACKENDS")
                rows = await cur.fetchall()
                alive = 0
                for row in rows:
                    record = dict(row)
                    value = record.get("Alive")
                    # Modern builds return a bool; older ones return 1/0 or
                    # 'true'/'false'. A row with no Alive column is counted only
                    # if the listing is known to include only healthy backends —
                    # absent that signal, treat it as alive.
                    if value is None or str(value).strip().lower() in (
                        "1",
                        "true",
                    ):
                        alive += 1
                return max(alive, 1) if rows else 1
        except Exception:
            return None
        finally:
            conn.close()

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
        system = {"_statistics_", "information_schema", "sys", "nova_system"}
        return [row[0] for row in rows if str(row[0]).casefold() not in system]

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
        """UDFs via ``SHOW FULL FUNCTIONS`` (best-effort; absent on some setups).

        StarRocks 4.1.4 has **no** ``SHOW CREATE FUNCTION``. For an SQL UDF the
        body is nonetheless recoverable: ``SHOW FULL FUNCTIONS`` returns it in the
        ``Properties`` column (e.g. ``"`x` + `y`"``), so the original definition is
        reconstructed faithfully. Java/Python UDFs carry a jar/payload in the same
        column that the column set alone cannot reproduce and stay ``lossy``.
        """
        return await self._functions(conn, database, global_scope=False)

    async def list_global_functions(self, conn: asyncmy.Connection) -> list[dict]:
        """Global SQL UDFs via ``SHOW GLOBAL FUNCTIONS``.

        Global functions are not scoped to a database, so they are enumerated once
        per source rather than per database. The verdict is the same as for a
        database-scoped function: reconstructable only when the body is native SQL.
        """
        return await self._functions(conn, None, global_scope=True)

    async def _functions(
        self, conn: asyncmy.Connection, database: str | None, *, global_scope: bool
    ) -> list[dict]:
        # ``SHOW GLOBAL FUNCTIONS`` lists only names; the full definition — needed
        # to reconstruct the body — is read with ``SHOW FULL GLOBAL FUNCTIONS``.
        # If that surface is absent on a build, the name-only listing is the
        # fallback. Nova never invents a body it did not read.
        statement = (
            "SHOW FULL GLOBAL FUNCTIONS"
            if global_scope
            else f"SHOW FULL FUNCTIONS FROM `{database}`"
        )
        try:
            rows = await self._query(conn, statement)
        except Exception:
            if global_scope:
                # Fall back to the name-only surface; the function is then listed
                # but its definition cannot be reconstructed, which the verdict
                # reports as lossy through the missing body.
                try:
                    rows = await self._query(conn, "SHOW GLOBAL FUNCTIONS")
                except Exception:
                    return []
            else:
                return []
        functions = []
        for row in rows:
            signature = row[0] or ""
            name = signature.split("(")[0] if signature else str(signature)
            functions.append(
                {
                    "name": name,
                    "kind": "function",
                    "signature": signature,
                    "return_type": row[1] if len(row) > 1 else None,
                    "function_type": row[2] if len(row) > 2 else None,
                    "properties": row[4] if len(row) > 4 else None,
                    "scope": "global" if global_scope else "database",
                }
            )
        return functions

    async def list_tasks(self, conn: asyncmy.Connection, database: str) -> list[dict]:
        """TASKs via ``information_schema.tasks`` (no ``SHOW CREATE`` exists).

        The column is ``DATABASE`` (not ``DATABASE_NAME``) and the schedule and
        body live in ``SCHEDULE`` / ``DEFINITION``, which makes a ``CREATE TASK``
        reconstruction possible. When the engine does not expose tasks at all
        (some builds disable the feature), the read is empty.
        """
        try:
            rows = await self._query(
                conn,
                "SELECT TASK_NAME, SCHEDULE, DEFINITION, PROPERTIES "
                "FROM information_schema.tasks "
                "WHERE DATABASE = %s ORDER BY TASK_NAME",
                (database,),
            )
        except Exception:
            return []
        tasks = []
        for row in rows:
            tasks.append(
                {
                    "name": row[0],
                    "kind": "task",
                    "schedule": row[1] if len(row) > 1 else None,
                    "definition": row[2] if len(row) > 2 else None,
                    "properties": row[3] if len(row) > 3 else None,
                }
            )
        return tasks

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

    async def list_columns(
        self, conn: asyncmy.Connection, database: str, table: str
    ) -> list[tuple[str, str]]:
        """``(COLUMN_NAME, DATA_TYPE)`` for a table, in ordinal order.

        Used by the data mover to build an explicit column list, so the copy is
        independent of physical column order between source and target and of any
        partition/hidden column the engine might otherwise include.
        """
        rows = await self._query(
            conn,
            "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
            (database, table),
        )
        return [(row[0], row[1]) for row in rows]

    async def count_rows(self, conn: asyncmy.Connection, database: str, table: str) -> int | None:
        """``SELECT COUNT(*)`` over a table on the given connection."""
        rows = await self._query(conn, f"SELECT COUNT(*) FROM `{database}`.`{table}`")
        return int(rows[0][0]) if rows else None

    async def scalar(self, conn: asyncmy.Connection, statement: str) -> float | None:
        """Run a single-value aggregate and return it as a float (or ``None``)."""
        rows = await self._query(conn, statement)
        if not rows or rows[0][0] is None:
            return None
        try:
            return float(rows[0][0])
        except (TypeError, ValueError):
            return None

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
