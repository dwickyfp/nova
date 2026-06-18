"""Query execution against StarRocks — user-scoped and system-scoped."""

import time
from dataclasses import dataclass, field

import asyncmy
import asyncmy.cursors

from app.core.config import settings
from app.core.database import db
from app.core.exceptions import StarRocksError


@dataclass
class QueryResult:
    """Standardized query result."""

    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    original_sql: str = ""
    executed_sql: str = ""
    warnings: list[str] = field(default_factory=list)


class QueryRepository:
    """Execute SQL against StarRocks.

    Two modes:
    - execute_as_system: admin connection (for metadata queries)
    - execute_as_user: user connection (RBAC-respecting)
    """

    async def execute_as_system(
        self,
        sql: str,
        database: str | None = None,
    ) -> QueryResult:
        """Execute SQL as system admin."""
        start = time.monotonic()
        try:
            async with db.system_conn() as conn:
                if database:
                    await conn.select_db(database)
                async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                    await cur.execute(sql)
                    elapsed = (time.monotonic() - start) * 1000

                    if cur.description:
                        columns = [desc[0] for desc in cur.description]
                        raw_rows = await cur.fetchall()
                        rows = [list(r.values()) for r in raw_rows]
                        return QueryResult(
                            columns=columns,
                            rows=rows,
                            row_count=len(rows),
                            elapsed_ms=round(elapsed, 2),
                            executed_sql=sql,
                        )
                    return QueryResult(
                        affected_rows=cur.rowcount,
                        elapsed_ms=round(elapsed, 2),
                        executed_sql=sql,
                    )
        except asyncmy.errors.OperationalError as e:
            raise StarRocksError(f"Connection error: {e}")
        except asyncmy.errors.ProgrammingError as e:
            raise StarRocksError(f"SQL error: {e}")

    async def execute_as_user(
        self,
        sql: str,
        username: str,
        password: str,
        database: str | None = None,
    ) -> QueryResult:
        """Execute SQL as an authenticated user (RBAC-respecting)."""
        start = time.monotonic()
        try:
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
                async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                    await cur.execute(sql)
                    elapsed = (time.monotonic() - start) * 1000

                    if cur.description:
                        columns = [desc[0] for desc in cur.description]
                        raw_rows = await cur.fetchall()
                        rows = [list(r.values()) for r in raw_rows]
                        return QueryResult(
                            columns=columns,
                            rows=rows,
                            row_count=len(rows),
                            elapsed_ms=round(elapsed, 2),
                            executed_sql=sql,
                        )
                    return QueryResult(
                        affected_rows=cur.rowcount,
                        elapsed_ms=round(elapsed, 2),
                        executed_sql=sql,
                    )
            finally:
                conn.close()
        except asyncmy.errors.OperationalError as e:
            raise StarRocksError(f"Connection error: {e}")
        except asyncmy.errors.ProgrammingError as e:
            raise StarRocksError(f"SQL error: {e}")


query_repo = QueryRepository()
