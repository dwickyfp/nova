"""StarRocks connection factory — system pool + per-request user connections."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncmy
import asyncmy.cursors

from app.core.config import settings
from app.modules.assistant.measurements import (
    metadata_borrows_observed,
    metadata_execution_scope,
    metadata_reads_observed,
    record_metadata_borrow,
    record_metadata_read,
    record_metadata_read_failure,
)

logger = logging.getLogger(__name__)


def _discard_interrupted_connection(conn: asyncmy.Connection) -> None:
    conn.close()
    # asyncmy 0.2.11 close() clears the socket but leaves connected=True;
    # Pool.release would otherwise queue a connection with no reader.
    conn._connected = False


#: Session timezone pinned on every connection when ``NOVA_TIMEZONE`` is unset.
#: Matches ``init-nova.sql``'s ``SET GLOBAL time_zone``.
DEFAULT_TIMEZONE = "Asia/Jakarta"


def configured_timezone() -> str:
    """The session timezone Nova pins, read from config at call time.

    Whitespace is trimmed; an empty value falls back to
    :data:`DEFAULT_TIMEZONE`, so a deployment that clears the variable still gets
    a deterministic zone rather than the engine's global.
    """
    value = str(getattr(settings, "NOVA_TIMEZONE", "") or "").strip()
    return value or DEFAULT_TIMEZONE


def _quote(value: str) -> str:
    """Single-quote a SQL string literal, escaping embedded quotes.

    The timezone is operator-controlled config, but a stray quote must not break
    (or truncate) the session statement.
    """
    return "'" + value.replace("'", "''") + "'"


def _timezone_init_command() -> str:
    """The ``init_command`` every connection is opened with."""
    return f"SET time_zone = {_quote(configured_timezone())}"


class StarRocksConnectionFactory:
    """Manages StarRocks connections via MySQL protocol.

    Two connection modes:
    - System pool: admin connection for metadata/system queries (SHOW, DESCRIBE, etc.)
    - User connections: per-request, no pool, RBAC-respecting

    Every connection is opened with ``init_command`` pinning its session
    ``time_zone`` to :func:`configured_timezone`, so ``NOW()`` and naive DATETIME
    round-trips are stable across the pool and per-request connections alike.
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
            init_command=_timezone_init_command(),
        )

    async def close_system_pool(self) -> None:
        """Close the system pool. Call at shutdown."""
        if self._system_pool:
            self._system_pool.close()
            await self._system_pool.wait_closed()
            self._system_pool = None

    async def apply_global_time_zone(self) -> None:
        """Best-effort ``SET GLOBAL time_zone`` at startup.

        The per-session pin is the real guarantee; this only aligns the engine's
        global so tools that read ``@@time_zone`` agree. A missing privilege (or
        any other failure) is logged and swallowed rather than aborting startup.
        """
        sql = f"SET GLOBAL time_zone = {_quote(configured_timezone())}"
        try:
            await self.execute_system(sql)
        except Exception as exc:  # noqa: BLE001 - advisory; sessions are already pinned
            logger.warning("Could not set GLOBAL time_zone (advisory): %s", type(exc).__name__)

    @asynccontextmanager
    @metadata_borrows_observed
    async def system_conn(self) -> AsyncGenerator[asyncmy.Connection, None]:
        """Get an admin connection from the system pool.

        Usage:
            async with db.system_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SHOW DATABASES")
        """
        record_metadata_borrow()
        if not self._system_pool:
            raise RuntimeError("System pool not initialized. Call init_system_pool() first.")
        pool = self._system_pool
        discarded = False
        try:
            async with pool.acquire() as conn:
                try:
                    yield conn
                except (asyncio.CancelledError, TimeoutError):
                    discarded = True
                    _discard_interrupted_connection(conn)
                    raise
        finally:
            if discarded:
                # The pinned driver's disconnected-release path omits its
                # wakeup, so notify after the connection leaves the used set.
                async with pool.cond:
                    pool.cond.notify()

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
            init_command=_timezone_init_command(),
        )
        try:
            yield conn
        finally:
            conn.close()

    @metadata_reads_observed
    async def execute_system(
        self, sql: str, params: list | tuple | None = None
    ) -> dict:
        """Execute SQL as system admin. Returns standardized result dict.

        Returns:
            {"columns": [...], "rows": [...], "row_count": N} for SELECT
            {"columns": [], "rows": [], "affected": N} for DDL/DML
        """
        with metadata_execution_scope():
            try:
                async with (
                    self.system_conn() as conn,
                    conn.cursor(asyncmy.cursors.DictCursor) as cur,
                ):
                    try:
                        await cur.execute(sql, params)
                        if cur.description:
                            record_metadata_read()
                            columns = [desc[0] for desc in cur.description]
                            rows = await cur.fetchall()
                            return {
                                "columns": columns,
                                "rows": [list(r.values()) for r in rows],
                                "row_count": len(rows),
                            }
                        return {"columns": [], "rows": [], "affected": cur.rowcount}
                    except (asyncio.CancelledError, TimeoutError):
                        # Close before cursor cleanup can wait on the cancelled result.
                        _discard_interrupted_connection(conn)
                        raise
            except BaseException:
                record_metadata_read_failure()
                raise


# Singleton — initialized in main.py lifespan
db = StarRocksConnectionFactory()
