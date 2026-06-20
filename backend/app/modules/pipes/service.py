"""Pipe Manager service — CRUD for StarRocks continuous ingestion pipes.

Pipes wrap an ``INSERT INTO … SELECT FROM FILES(…)`` statement with
auto-ingest polling.  This service issues the corresponding DDL
(``CREATE PIPE``, ``ALTER PIPE``, ``DROP PIPE``, ``SHOW PIPES``) over
asyncmy and parses the results into schema-friendly dicts.
"""

import json
import logging

import asyncmy
import asyncmy.cursors

from app.core.config import settings

logger = logging.getLogger(__name__)


class PipeService:
    """Business logic for StarRocks pipe management."""

    # ── DB helpers ──────────────────────────────────────────────

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Create a direct asyncmy connection to StarRocks."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user="root",
            password="",
            autocommit=True,
        )

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _parse_properties(raw: str | None) -> dict[str, str]:
        """Parse the Properties column from SHOW PIPES into a dict.

        StarRocks returns properties as a semicolon-separated string like
        ``"AUTO_INGEST=true;POLL_INTERVAL=300;BATCH_SIZE=1GB"`` or sometimes
        as a JSON-ish blob.  We handle both formats gracefully.
        """
        if not raw:
            return {}

        raw = raw.strip()

        # Try JSON first
        if raw.startswith("{"):
            try:
                return {str(k): str(v) for k, v in json.loads(raw).items()}
            except (json.JSONDecodeError, AttributeError):
                pass

        # Semicolon / comma separated key=value pairs
        props: dict[str, str] = {}
        sep = ";" if ";" in raw else ","
        for token in raw.split(sep):
            token = token.strip().strip('"').strip("'")
            if "=" in token:
                k, v = token.split("=", 1)
                props[k.strip().strip('"')] = v.strip().strip('"')
        return props

    @staticmethod
    def _row_to_pipe(row: dict) -> dict:
        """Normalise a SHOW PIPES result row into PipeResponse shape."""
        # StarRocks column names may vary in casing — normalise to lower.
        lr = {k.lower(): v for k, v in row.items()}
        return {
            "name": lr.get("name", ""),
            "database": lr.get("database", ""),
            "state": lr.get("state", "UNKNOWN"),
            "sql": lr.get("sql") or lr.get("source") or None,
            "properties": PipeService._parse_properties(lr.get("properties")),
            "created_at": str(lr["create_time"]) if lr.get("create_time") else None,
        }

    # ── Pipe CRUD ──────────────────────────────────────────────

    async def list_pipes(self, database: str | None = None) -> list[dict]:
        """Execute ``SHOW PIPES`` (optionally scoped to a database)."""
        sql = "SHOW PIPES"
        if database:
            sql += f" FROM `{database}`"

        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
                return [self._row_to_pipe(r) for r in rows]
        finally:
            conn.close()

    async def get_pipe(self, name: str, database: str | None = None) -> dict | None:
        """Return a single pipe by name, or ``None`` if not found."""
        sql = f"SHOW PIPES LIKE '{name}'"
        if database:
            sql = f"SHOW PIPES FROM `{database}` LIKE '{name}'"

        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(sql)
                row = await cur.fetchone()
                return self._row_to_pipe(row) if row else None
        finally:
            conn.close()

    async def create_pipe(self, data: dict) -> dict:
        """Build and execute ``CREATE PIPE … PROPERTIES (…) AS <sql>``.

        Returns the newly created pipe via ``get_pipe``.
        """
        name = data["name"]
        auto_ingest = "TRUE" if data.get("auto_ingest", True) else "FALSE"
        poll_interval = str(data.get("poll_interval", 300))
        batch_size = data.get("batch_size", "1GB")
        batch_files = str(data.get("batch_files", 256))
        pipe_sql = data["sql"]
        database = data.get("database", "")

        qualified_name = f"`{database}`.`{name}`" if database else f"`{name}`"

        ddl = (
            f"CREATE PIPE {qualified_name}\n"
            f"PROPERTIES (\n"
            f'    "AUTO_INGEST" = "{auto_ingest}",\n'
            f'    "POLL_INTERVAL" = "{poll_interval}",\n'
            f'    "BATCH_SIZE" = "{batch_size}",\n'
            f'    "BATCH_FILES" = "{batch_files}"\n'
            f")\n"
            f"AS {pipe_sql}"
        )

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(ddl)
        finally:
            conn.close()

        # Return the freshly created pipe
        result = await self.get_pipe(name, database=database or None)
        return result or {"name": name, "database": database, "state": "UNKNOWN"}

    async def suspend_pipe(self, name: str, database: str | None = None) -> dict:
        """Execute ``ALTER PIPE … SUSPEND``."""
        qualified_name = f"`{database}`.`{name}`" if database else f"`{name}`"
        sql = f"ALTER PIPE {qualified_name} SUSPEND"

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql)
        finally:
            conn.close()

        return {"success": True, "message": f"Pipe '{name}' suspended"}

    async def resume_pipe(self, name: str, database: str | None = None) -> dict:
        """Execute ``ALTER PIPE … RESUME``."""
        qualified_name = f"`{database}`.`{name}`" if database else f"`{name}`"
        sql = f"ALTER PIPE {qualified_name} RESUME"

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql)
        finally:
            conn.close()

        return {"success": True, "message": f"Pipe '{name}' resumed"}

    async def drop_pipe(self, name: str, database: str | None = None) -> dict:
        """Execute ``DROP PIPE …``."""
        qualified_name = f"`{database}`.`{name}`" if database else f"`{name}`"
        sql = f"DROP PIPE {qualified_name}"

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql)
        finally:
            conn.close()

        return {"success": True, "message": f"Pipe '{name}' dropped"}

    # ── Pipe Files ─────────────────────────────────────────────

    async def list_pipe_files(self, name: str, database: str | None = None) -> list[dict]:
        """Query ``information_schema.pipe_files`` for the given pipe."""
        where = f"pipe_name = '{name}'"
        if database:
            where += f" AND database_name = '{database}'"

        sql = (
            "SELECT pipe_name, file_name, state, file_size, "
            "error_message, last_modified "
            f"FROM information_schema.pipe_files "
            f"WHERE {where} "
            f"ORDER BY last_modified DESC"
        )

        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
                return [
                    {
                        "file_name": r.get("file_name") or r.get("FILE_NAME", ""),
                        "state": r.get("state") or r.get("STATE", "UNKNOWN"),
                        "file_size": r.get("file_size") or r.get("FILE_SIZE"),
                        "error_message": r.get("error_message") or r.get("ERROR_MESSAGE"),
                        "loaded_at": str(r["last_modified"]) if r.get("last_modified") or r.get("LAST_MODIFIED") else None,
                    }
                    for r in rows
                ]
        finally:
            conn.close()


# Singleton
pipe_service = PipeService()
