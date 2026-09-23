"""Query execution against StarRocks — user-scoped and system-scoped.

``executed_sql`` is the one field that carries the statement Nova actually ran —
post ``@stage`` → ``FILES()`` translation, with real storage credentials
injected because the engine needs them. Every such statement leaves the process
twice (API JSON body, ``NOVA_SYSTEM.AUDIT_LOG``) and both destinations are on
the never-store-credentials list in AGENTS.md §2.

Redaction therefore lives here, on the constructor: this is the single point at
which ``executed_sql`` can enter a ``QueryResult``, so no future code path —
method, router or helper — can forget it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import asyncmy
import asyncmy.cursors

from app.common.identifiers import check_identifier
from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import StarRocksError


@dataclass
class QueryResult:
    """Standardized query result.

    ``executed_sql`` is always the *redacted* form; pass the statement verbatim
    and it comes back with credential values replaced by ``***``.

    ``error`` is the explicit failure marker and the single source of the
    ``success`` contract (``x-request-id`` aside, ``POST /query/execute``
    serialises this object). It is ``None`` on success and carries the failure
    message otherwise. Before it existed the router *inferred* failure from the
    shape of the result — ``warnings`` non-empty and no columns and no rows —
    which misreported every successful ``@stage`` DML statement, because
    ``translate_stage_query`` always appends a warning on the success path
    (``dialect/translator.py``) while DML returns no ``description``
    (``columns=[]``, ``row_count=0``).

    The failure sites set it explicitly:

    - repository execution errors — raised as ``StarRocksError``, so no result
      object reaches the caller;
    - ``execute_statements`` — the statement that raised becomes
      ``error=str(exc)``;
    - ``translate_stage_query`` — the statement Nova refused to run becomes
      ``error=str(exc)``.

    ``warnings`` stays an informational channel for *non-fatal* notices and
    never decides failure.
    """

    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    original_sql: str = ""
    executed_sql: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def __post_init__(self) -> None:
        self.executed_sql = redact_sql_credentials(self.executed_sql)

    @property
    def success(self) -> bool:
        """Whether the statement executed.

        Derived from the explicit marker only — never from the shape of the
        result. See the class docstring for why that inference was wrong.
        """
        return self.error is None


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
            raise StarRocksError(f"Connection error: {e}") from e
        except asyncmy.errors.ProgrammingError as e:
            raise StarRocksError(f"SQL error: {e}") from e

    async def execute_as_user(
        self,
        sql: str,
        username: str,
        password: str,
        database: str | None = None,
        role: str | None = None,
        max_rows: int | None = None,
        connected: asyncmy.Connection | None = None,
    ) -> QueryResult:
        """Execute SQL as an authenticated user (RBAC-respecting).

        ``connected`` lets a caller supply a connection that is **already
        authenticated** instead of one this method opens from ``username`` and
        ``password``. The MySQL proxy needs this: it authenticates by relaying
        StarRocks' own challenge (see ``app/proxy/auth.py``), so it holds a live
        authenticated socket and never a plaintext password to hand this
        method. Nothing else changes — the statement still runs as the user
        whose credentials opened the connection, so RBAC is whatever StarRocks
        granted that session.

        ``database`` is selected after ``SET ROLE`` on either connection path.
        A user may have access through a non-default role, so selecting the
        database during connection setup would reject an authorized query.
        """
        if settings.RANGER_ENABLED and not role:
            raise StarRocksError("User-data execution requires exactly one explicit active role")
        start = time.monotonic()
        if connected is not None:
            return await self._execute_on(
                connected, sql, role=role, database=database, max_rows=max_rows, start=start
            )
        try:
            async with db.user_conn(
                username=username,
                password=password,
                database=None,
            ) as conn:
                return await self._execute_on(
                    conn, sql, role=role, database=database, max_rows=max_rows, start=start
                )
        except asyncmy.errors.OperationalError as e:
            raise StarRocksError(f"Connection error: {e}") from e
        except asyncmy.errors.ProgrammingError as e:
            raise StarRocksError(f"SQL error: {e}") from e

    @staticmethod
    async def _execute_on(
        conn: asyncmy.Connection,
        sql: str,
        *,
        role: str | None,
        max_rows: int | None,
        start: float,
        database: str | None = None,
    ) -> QueryResult:
        try:
            cursor_type = asyncmy.cursors.SSDictCursor if max_rows else asyncmy.cursors.DictCursor
            async with conn.cursor(cursor_type) as cur:
                if role:
                    await cur.execute(f"SET ROLE {check_identifier(role, field='role')}")
                if database:
                    await conn.select_db(database)
                await cur.execute(sql)
                elapsed = (time.monotonic() - start) * 1000

                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    raw_rows = await cur.fetchmany(max_rows) if max_rows else await cur.fetchall()
                    rows = [
                        list(r.values()) if isinstance(r, Mapping) else list(r) for r in raw_rows
                    ]
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
            raise StarRocksError(f"Connection error: {e}") from e
        except asyncmy.errors.ProgrammingError as e:
            raise StarRocksError(f"SQL error: {e}") from e

    @staticmethod
    async def _set_role(cur: asyncmy.cursors.DictCursor, role: str) -> None:
        await cur.execute(f"SET ROLE {check_identifier(role, field='role')}")


query_repo = QueryRepository()
