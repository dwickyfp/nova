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

import asyncio
import time
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from typing import Protocol

import asyncmy
import asyncmy.cursors
from asyncmy.constants import FIELD_TYPE
from asyncmy.converters import through

from app.common.identifiers import check_identifier
from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import StarRocksError


def _convert_ascii(converter, value):
    return converter(value.decode("ascii") if isinstance(value, bytes) else value)


@contextmanager
def _binary_result_mode(conn, enabled: bool):
    if not enabled or not isinstance(conn, asyncmy.Connection):
        yield False
        return
    unicode_mode, decoders = conn._use_unicode, conn._decoders
    try:
        # StarRocks geometry is opaque binary carried in a VARCHAR column.
        conn._use_unicode = False
        conn._decoders = {
            code: partial(_convert_ascii, converter)
            if converter is not None and converter is not through else converter
            for code, converter in decoders.items()
        }
        yield True
    finally:
        conn._use_unicode, conn._decoders = unicode_mode, decoders


def _decode_result_rows(cursor, raw_rows):
    fields = cursor._result.fields
    rows = []
    for row in raw_rows:
        values = list(row.values()) if isinstance(row, Mapping) else list(row)
        for index, value in enumerate(values):
            field = fields[index]
            if isinstance(value, bytes) and (
                field.charsetnr != 63 or field.type_code == FIELD_TYPE.JSON
            ):
                with suppress(UnicodeDecodeError):
                    values[index] = value.decode("utf-8")
        rows.append(values)
    return rows


#: Rows fetched per round trip when a result is streamed to a row sink.
_STREAM_BATCH_ROWS = 1000


class RowSink(Protocol):
    """Receives a result set as it arrives instead of after it is complete."""

    async def begin(self, columns: list[str], column_types: tuple[str, ...]) -> None: ...

    async def rows(self, rows: list[list]) -> None: ...


def _starrocks_error(exc: Exception) -> StarRocksError:
    """Translate a driver error, keeping the engine's own error number.

    A server-side failure (an analysis error, an unknown table, a refused
    privilege) is reported by its engine message and code. Codes 2000-2999 are
    the client library's own (lost connection, server gone away), which keep
    the connection-error wording. Every driver class is covered: the engine
    reports a filtered insert as an ``InternalError`` and a constraint failure as
    an ``IntegrityError``, and those must not reach callers in the driver's raw
    ``(code, 'message')`` form.
    """
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    if code is not None and not 2000 <= code < 3000:
        detail = exc.args[1] if len(exc.args) > 1 else ""
        return StarRocksError(f"SQL error: ({code}) {detail}", engine_code=code)
    if isinstance(exc, asyncmy.errors.OperationalError):
        return StarRocksError(f"Connection error: {exc}")
    return StarRocksError(f"SQL error: {exc}")


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
    destructive: bool | None = None
    needs_confirmation: bool = False
    error_code: str | None = None
    statement_kind: str | None = None
    effects: dict[str, bool] | None = None
    execution_failure: dict | None = None
    nova_execution_id: str | None = None
    engine_query_ids: list[str] = field(default_factory=list)
    correlation_status: str = "unavailable"
    column_types: tuple[str, ...] = ()
    truncated: bool = False
    #: Rows were handed to a row sink as they arrived instead of kept in ``rows``.
    streamed: bool = False
    engine_error_code: int | None = None
    fetch_ms: float | None = None
    engine_roundtrip_ms: float | None = None
    engine_ms: float | None = None
    nova_ms: float | None = None
    total_ms: float | None = None

    def __post_init__(self) -> None:
        self.original_sql = redact_sql_credentials(self.original_sql)
        self.executed_sql = redact_sql_credentials(self.executed_sql)
        if self.error:
            self.error = redact_sql_credentials(self.error)
        self.redact_metadata()

    def redact_metadata(self) -> None:
        if {"Signature", "Function Type", "Properties"}.issubset(self.columns):
            index = self.columns.index("Properties")
            self.rows = [
                [
                    redact_sql_credentials(value)
                    if position == index and isinstance(value, str)
                    else value
                    for position, value in enumerate(row)
                ]
                for row in self.rows
            ]

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
        except asyncmy.errors.DatabaseError as e:
            raise _starrocks_error(e) from e

    async def execute_as_user(
        self,
        sql: str,
        username: str,
        password: str,
        database: str | None = None,
        role: str | None = None,
        max_rows: int | None = None,
        connected: asyncmy.Connection | None = None,
        session_prepared: bool = False,
        row_sink: RowSink | None = None,
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
                connected,
                sql,
                role=role,
                database=database,
                max_rows=max_rows,
                start=start,
                session_prepared=session_prepared,
                binary_results=True,
                row_sink=row_sink,
            )
        try:
            async with db.user_conn(
                username=username,
                password=password,
                database=None,
            ) as conn:
                return await self._execute_on(
                    conn,
                    sql,
                    role=role,
                    database=database,
                    max_rows=max_rows,
                    start=start,
                    row_sink=row_sink,
                )
        except asyncmy.errors.DatabaseError as e:
            raise _starrocks_error(e) from e

    @staticmethod
    async def _execute_on(
        conn: asyncmy.Connection,
        sql: str,
        *,
        role: str | None,
        max_rows: int | None,
        start: float,
        database: str | None = None,
        session_prepared: bool = False,
        binary_results: bool = False,
        row_sink: RowSink | None = None,
    ) -> QueryResult:
        from app.modules.query_autopilot.telemetry import EXECUTION, collector
        from app.sql_frontend.session_functions import CORRELATION_SESSION, preserve_last_query_id

        identity = EXECUTION.get()
        session = CORRELATION_SESSION.get()
        wire_sql, labels = preserve_last_query_id(sql, session)
        restore_profile = False
        try:
            if identity is not None and identity.capture_profile:
                try:
                    async with conn.cursor() as setup:
                        await setup.execute("SELECT @@enable_profile")
                        previous = await setup.fetchone()
                        enabled = (
                            next(iter(previous.values()))
                            if isinstance(previous, Mapping)
                            else previous[0]
                        )
                        if not bool(int(enabled)):
                            # Mark before the write: an uncertain SET still needs restoration.
                            restore_profile = True
                            await setup.execute("SET enable_profile = true")
                        identity.profile_enabled = True
                except Exception:
                    identity.profile_enabled = False

            # A streamed result is read as tuples: one dict per row is the
            # largest per-row cost on that path, and the sink needs positions only.
            cursor_type = (
                asyncmy.cursors.SSCursor
                if row_sink is not None
                else asyncmy.cursors.SSDictCursor
                if max_rows
                else asyncmy.cursors.DictCursor
            )
            with _binary_result_mode(conn, binary_results) as raw_text:
                async with conn.cursor(cursor_type) as cur:
                    if role and not session_prepared:
                        await cur.execute(f"SET ROLE {check_identifier(role, field='role')}")
                    if database and not session_prepared:
                        await conn.select_db(database)
                    submitted = time.monotonic()
                    await cur.execute(wire_sql)
                    fetched = time.monotonic()
                    result = QueryResult(executed_sql=sql)
                    if cur.description:
                        result.columns = [
                            labels.get(i, desc[0]) for i, desc in enumerate(cur.description)
                        ]
                        result.column_types = tuple(
                            str((desc[1], desc[4], desc[5])) for desc in cur.description
                        )
                        if row_sink is not None:
                            await row_sink.begin(result.columns, result.column_types)
                            while batch := await cur.fetchmany(_STREAM_BATCH_ROWS):
                                rows = (
                                    _decode_result_rows(cur, batch)
                                    if raw_text
                                    else [
                                        list(r.values()) if isinstance(r, Mapping) else list(r)
                                        for r in batch
                                    ]
                                )
                                result.row_count += len(rows)
                                await row_sink.rows(rows)
                            result.streamed = True
                            raw_rows = None
                        else:
                            raw_rows = (
                                await cur.fetchmany(max_rows + 1)
                                if max_rows
                                else await cur.fetchall()
                            )
                        if raw_rows is not None:
                            result.truncated = bool(max_rows and len(raw_rows) > max_rows)
                            if max_rows:
                                raw_rows = raw_rows[:max_rows]
                            result.rows = (
                                _decode_result_rows(cur, raw_rows) if raw_text else [
                                    list(r.values()) if isinstance(r, Mapping) else list(r)
                                    for r in raw_rows
                                ]
                            )
                            result.row_count = len(result.rows)
                    else:
                        result.affected_rows = cur.rowcount
            result.fetch_ms = (time.monotonic() - fetched) * 1000
            # Closing an unbuffered cursor drains the wire before asking the same
            # authenticated session for its engine identity.
            result.elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            result.engine_roundtrip_ms = (time.monotonic() - submitted) * 1000
            if identity is not None:
                identity.engine_roundtrip_ms += result.engine_roundtrip_ms
                identity.fetch_ms += result.fetch_ms
            from app.modules.query_autopilot.telemetry import PURPOSE

            if identity is not None and (collector.enabled or PURPOSE.get() != "workload"):
                try:
                    async with conn.cursor(asyncmy.cursors.DictCursor) as correlation:
                        identity.scope_checked = True
                        await correlation.execute(
                            "SELECT LAST_QUERY_ID() AS nova_engine_query_id, "
                            "CATALOG() AS nova_catalog, DATABASE() AS nova_database"
                        )
                        row = await correlation.fetchone()
                        if isinstance(row, Mapping):
                            catalog = row.get("nova_catalog")
                            database_context = row.get("nova_database")
                            if isinstance(catalog, str) and catalog:
                                identity.catalog = catalog
                                identity.database = (
                                    database_context if isinstance(database_context, str) else None
                                )
                        value = (
                            row.get("nova_engine_query_id") if isinstance(row, Mapping) else row[0]
                        )
                        from uuid import UUID

                        engine_id = str(UUID(str(value)))
                        identity.query_ids.append(engine_id)
                        result.engine_query_ids = [engine_id]
                        result.correlation_status = "available"
                        if session is not None:
                            session.last_query_id = engine_id
                            session.instrumented = True
                except Exception:
                    # Never retry the user's statement because telemetry failed.
                    if session is not None:
                        session.last_query_id = None
                        session.instrumented = True
            elif session is not None:
                session.instrumented = False
            result.redact_metadata()
            return result
        except asyncio.CancelledError:
            conn.close()
            raise
        except asyncmy.errors.DatabaseError as e:
            raise _starrocks_error(e) from e
        finally:
            if restore_profile:
                try:
                    async with conn.cursor() as cleanup:
                        await cleanup.execute("SET enable_profile = false")
                except Exception:
                    # A session whose setting cannot be restored must not be reused.
                    conn.close()

    @staticmethod
    async def _set_role(cur: asyncmy.cursors.DictCursor, role: str) -> None:
        await cur.execute(f"SET ROLE {check_identifier(role, field='role')}")


query_repo = QueryRepository()
