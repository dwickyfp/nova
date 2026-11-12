"""Object Browser repository — StarRocks metadata queries.

Identifiers reach these queries as URL path segments, so each is validated
against the shared allow-list before it is interpolated into a ``SHOW``/``DESC``
statement (NOVA-89). The queries already run on the caller's connection; the
validation is what keeps a path segment from breaking out of the identifier.

The caller-supplied ``role`` is an identifier too (NOVA-101): it is the role
the caller asks to ``SET ROLE`` to, not the session's own role, so it is
allow-listed before it is interpolated — a strip-only sanitizer let a ``;``
open a second statement.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncmy.errors

from app.common.identifiers import check_identifier
from app.core.database import db
from app.core.security import decrypt_password

#: Engine errors for a database that no longer exists (dropped while listing).
_MISSING_DATABASE = {1049, 5501}


class ObjectRepository:
    """Query StarRocks metadata for the object browser tree."""

    async def _execute_user(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        database: str | None = None,
        role: str | None = None,
    ) -> dict:
        password = decrypt_password(encrypted_password)
        async with (
            db.user_conn(username, password, database=database) as conn,
            conn.cursor() as cur,
        ):
            if role:
                await cur.execute(f"SET ROLE {check_identifier(role, field='role')}")
            await cur.execute(sql)
            if cur.description:
                rows = await cur.fetchall()
                return {"rows": rows}
            return {"rows": []}

    # ── Databases ──────────────────────────────────────────────

    async def list_databases(
        self,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """List all databases (excluding system catalogs)."""
        result = await self._execute_user(
            "SHOW DATABASES",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )
        databases = []
        for row in result["rows"]:
            name = row[0]
            # Skip internal StarRocks databases
            if name in ("_statistics_", "information_schema", "sys"):
                continue
            databases.append({"name": name})
        return databases

    async def get_database(
        self,
        name: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict | None:
        """Get database details."""
        name = check_identifier(name, field="database")
        result = await self._execute_user(
            f"SHOW CREATE DATABASE `{name}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
        )
        if not result["rows"]:
            return None
        return {"name": name, "ddl": result["rows"][0][1]}

    # ── Tables ─────────────────────────────────────────────────

    async def list_tables(
        self,
        database: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """List all tables in a database."""
        database = check_identifier(database, field="database")
        result = await self._execute_user(
            f"SHOW TABLES FROM `{database}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        tables = []
        for row in result["rows"]:
            name = row[0]
            tables.append({"name": name, "database": database, "type": "TABLE"})
        return tables

    async def get_table_detail(
        self,
        database: str,
        table: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict | None:
        """Get full table detail — columns, indexes, partition, DDL."""
        database = check_identifier(database, field="database")
        table = check_identifier(table, field="table name")
        # Column info via DESC
        try:
            desc_result = await self._execute_user(
                f"DESC `{database}`.`{table}`",
                username=username,
                encrypted_password=encrypted_password,
                role=role,
                database=database,
            )
        except Exception:
            return None  # Table doesn't exist
        columns = []
        for row in desc_result["rows"]:
            columns.append({
                "name": row[0],
                "type": row[1],
                "null": row[2],
                "key": row[3],
                "default": row[4],
                "extra": row[5] if len(row) > 5 else "",
            })

        # DDL
        ddl_result = await self._execute_user(
            f"SHOW CREATE TABLE `{database}`.`{table}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        ddl = ddl_result["rows"][0][1] if ddl_result["rows"] else ""

        # Table status (engine, rows, size, etc.)
        status_result = await self._execute_user(
            f"SHOW TABLE STATUS FROM `{database}` LIKE '{table}'",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        status = {}
        if status_result["rows"]:
            row = status_result["rows"][0]
            status = {
                "engine": row[1] if len(row) > 1 else "",
                "rows": row[4] if len(row) > 4 else 0,
                "data_length": row[6] if len(row) > 6 else 0,
                "index_length": row[8] if len(row) > 8 else 0,
            }

        return {
            "name": table,
            "database": database,
            "type": "TABLE",
            "columns": columns,
            "column_count": len(columns),
            "ddl": ddl,
            "status": status,
        }

    # ── Views ──────────────────────────────────────────────────

    async def list_views(
        self,
        database: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """List all views in a database."""
        database = check_identifier(database, field="database")
        result = await self._execute_user(
            f"SHOW TABLES FROM `{database}` LIKE '%'"  # Get all, filter in Python
            ,
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        views = []
        for row in result["rows"]:
            name = row[0]
            # Check if it's a view by trying SHOW CREATE VIEW
            try:
                view_result = await self._execute_user(
                    f"SHOW CREATE VIEW `{database}`.`{name}`",
                    username=username,
                    encrypted_password=encrypted_password,
                    role=role,
                    database=database,
                )
                if view_result["rows"]:
                    views.append({"name": name, "database": database, "type": "VIEW"})
            except Exception:
                pass  # Not a view, skip
        return views

    async def get_view_detail(
        self,
        database: str,
        view: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict | None:
        """Get view detail — definition, columns."""
        database = check_identifier(database, field="database")
        view = check_identifier(view, field="view name")
        try:
            ddl_result = await self._execute_user(
                f"SHOW CREATE VIEW `{database}`.`{view}`",
                username=username,
                encrypted_password=encrypted_password,
                role=role,
                database=database,
            )
            ddl = ddl_result["rows"][0][1] if ddl_result["rows"] else ""
        except Exception:
            return None

        # Get column info
        desc_result = await self._execute_user(
            f"DESC `{database}`.`{view}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        columns = []
        for row in desc_result["rows"]:
            columns.append({
                "name": row[0],
                "type": row[1],
                "null": row[2],
                "key": row[3],
                "default": row[4],
                "extra": row[5] if len(row) > 5 else "",
            })

        return {
            "name": view,
            "database": database,
            "type": "VIEW",
            "columns": columns,
            "column_count": len(columns),
            "ddl": ddl,
        }

    # ── Materialized Views ─────────────────────────────────────

    async def list_materialized_views(
        self,
        database: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> list[dict]:
        """List all materialized views in a database."""
        database = check_identifier(database, field="database")
        result = await self._execute_user(
            f"SHOW ALTER MATERIALIZED VIEW FROM `{database}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        mvs = []
        if result["rows"]:
            for row in result["rows"]:
                mvs.append({"name": row[0], "database": database, "type": "MATERIALIZED_VIEW"})
        return mvs

    # ── Table Types ────────────────────────────────────────────

    @asynccontextmanager
    async def _user_cursor(
        self, *, username: str, encrypted_password: str, role: str | None = None,
    ) -> AsyncIterator[Any]:
        """One caller connection, with the requested role set once."""
        password = decrypt_password(encrypted_password)
        async with db.user_conn(username, password) as conn, conn.cursor() as cur:
            if role:
                await cur.execute(f"SET ROLE {check_identifier(role, field='role')}")
            yield cur

    @staticmethod
    async def _table_types(cur: Any, database: str) -> dict[str, list[dict]]:
        """Tables, views and materialized views of one database, in two statements.

        ``SHOW FULL TABLES`` types each relation; it reports a materialized view
        as ``VIEW``, so the database's materialized views are listed once and
        moved out of the views.
        """
        database = check_identifier(database, field="database")
        await cur.execute(f"SHOW FULL TABLES FROM `{database}`")
        relations = [(str(row[0]), str(row[1]).upper()) for row in await cur.fetchall()]
        materialized: set[str] = set()
        try:
            await cur.execute(f"SHOW MATERIALIZED VIEWS FROM `{database}`")
            columns = [d[0].lower() for d in cur.description or ()]
            at = columns.index("name") if "name" in columns else 0
            materialized = {str(row[at]) for row in await cur.fetchall()}
        except Exception:  # noqa: BLE001 - a database without the privilege lists none
            materialized = set()
        tables: list[dict] = []
        views: list[dict] = []
        mvs: list[dict] = []
        for name, kind in relations:
            if name in materialized:
                mvs.append({"name": name, "database": database, "type": "MATERIALIZED_VIEW"})
            elif kind == "VIEW":
                views.append({"name": name, "database": database, "type": "VIEW"})
            else:
                tables.append({"name": name, "database": database, "type": "TABLE"})
        return {"tables": tables, "views": views, "materialized_views": mvs}

    async def list_table_types(
        self,
        database: str,
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
    ) -> dict[str, list[dict]]:
        """List all tables and views in a database, categorized.

        Returns: {"tables": [...], "views": [...], "materialized_views": [...]}
        """
        async with self._user_cursor(
            username=username, encrypted_password=encrypted_password, role=role,
        ) as cur:
            return await self._table_types(cur, database)

    async def table_types_by_database(
        self,
        databases: list[str],
        *,
        username: str,
        encrypted_password: str,
        role: str | None = None,
        connections: int = 4,
    ) -> dict[str, dict[str, list[dict]]]:
        """Categorized relations for many databases over a few caller connections.

        Each connection carries the caller's identity and requested role, so the
        engine applies the same access as one connection per statement did.
        A database dropped while listing has nothing to list.
        """
        pending = list(databases)
        found: dict[str, dict[str, list[dict]]] = {}

        async def work() -> None:
            async with self._user_cursor(
                username=username, encrypted_password=encrypted_password, role=role,
            ) as cur:
                while pending:
                    name = pending.pop(0)
                    try:
                        found[name] = await self._table_types(cur, name)
                    except asyncmy.errors.Error as error:
                        if not error.args or error.args[0] not in _MISSING_DATABASE:
                            raise
                        found[name] = {"tables": [], "views": [], "materialized_views": []}

        if pending:
            await asyncio.gather(*(work() for _ in range(min(connections, len(pending)))))
        return found


object_repo = ObjectRepository()
