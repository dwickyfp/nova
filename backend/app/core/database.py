"""StarRocks connection factory — system pool + per-request user connections."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncmy
import asyncmy.cursors

from app.core.config import settings


class StarRocksConnectionFactory:
    """Manages StarRocks connections via MySQL protocol.

    Two connection modes:
    - System pool: admin connection for metadata/system queries (SHOW, DESCRIBE, etc.)
    - User connections: per-request, no pool, RBAC-respecting
    """

    def __init__(self):
        self._system_pool: asyncmy.Pool | None = None

    async def init_system_pool(self) -> None:
        """Create the system connection pool. Call once at startup."""
        self._system_pool = await asyncmy.create_pool(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            minsize=2,
            maxsize=10,
            autocommit=True,
            connect_timeout=10,
        )

    async def close_system_pool(self) -> None:
        """Close the system pool. Call at shutdown."""
        if self._system_pool:
            self._system_pool.close()
            await self._system_pool.wait_closed()
            self._system_pool = None

    @asynccontextmanager
    async def system_conn(self) -> AsyncGenerator[asyncmy.Connection, None]:
        """Get an admin connection from the system pool.

        Usage:
            async with db.system_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SHOW DATABASES")
        """
        if not self._system_pool:
            raise RuntimeError("System pool not initialized. Call init_system_pool() first.")
        async with self._system_pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def user_conn(
        self,
        username: str,
        password: str,
        database: str | None = None,
    ) -> AsyncGenerator[asyncmy.Connection, None]:
        """Create a per-request user connection (no pool, RBAC-respecting).

        Usage:
            async with db.user_conn("analyst", "pass123") as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT * FROM my_table")
        """
        conn = await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=username,
            password=password,
            database=database,
            autocommit=True,
            connect_timeout=10,
            read_timeout=300,
        )
        try:
            yield conn
        finally:
            conn.close()

    async def execute_system(
        self, sql: str, params: list | tuple | None = None
    ) -> dict:
        """Execute SQL as system admin. Returns standardized result dict.

        Returns:
            {"columns": [...], "rows": [...], "row_count": N} for SELECT
            {"columns": [], "rows": [], "affected": N} for DDL/DML
        """
        async with self.system_conn() as conn:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(sql, params)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = await cur.fetchall()
                    return {
                        "columns": columns,
                        "rows": [list(r.values()) for r in rows],
                        "row_count": len(rows),
                    }
                return {"columns": [], "rows": [], "affected": cur.rowcount}


# Singleton — initialized in main.py lifespan
db = StarRocksConnectionFactory()
