"""Object Browser repository — StarRocks metadata queries."""

from app.core.security import decrypt_password
from app.core.database import db


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
        async with db.user_conn(username, password, database=database) as conn:
            async with conn.cursor() as cur:
                if role:
                    safe_role = role.replace("`", "").replace("'", "")
                    await cur.execute(f"SET ROLE {safe_role}")
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

    # ── Table Types (efficient single query) ───────────────────

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
        result = await self._execute_user(
            f"SHOW TABLES FROM `{database}`",
            username=username,
            encrypted_password=encrypted_password,
            role=role,
            database=database,
        )
        tables = []
        views = []
        mvs = []

        for row in result["rows"]:
            name = row[0]
            # Try to detect type by checking SHOW CREATE VIEW
            try:
                await self._execute_user(
                    f"SHOW CREATE VIEW `{database}`.`{name}`",
                    username=username,
                    encrypted_password=encrypted_password,
                    role=role,
                    database=database,
                )
                views.append({"name": name, "database": database, "type": "VIEW"})
            except Exception:
                tables.append({"name": name, "database": database, "type": "TABLE"})

        # Check for materialized views
        try:
            mv_result = await self._execute_user(
                f"SHOW ALTER MATERIALIZED VIEW FROM `{database}`",
                username=username,
                encrypted_password=encrypted_password,
                role=role,
                database=database,
            )
            if mv_result["rows"]:
                for row in mv_result["rows"]:
                    mvs.append({"name": row[0], "database": database, "type": "MATERIALIZED_VIEW"})
        except Exception:
            pass

        return {"tables": tables, "views": views, "materialized_views": mvs}


object_repo = ObjectRepository()
