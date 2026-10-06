"""Audit helpers for Nova user actions."""

from __future__ import annotations

import asyncio
import contextvars
import json
import uuid
import weakref

from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.core.database import db

#: Byte budget for each ``AUDIT_LOG`` text column. The columns are StarRocks
#: ``STRING``/``TEXT`` (at most 65533 bytes); an over-long value makes the audit
#: insert fail after the audited statement already ran, and the client would see
#: that failure instead of the statement's result.
AUDIT_TEXT_MAX_BYTES = 60_000


def fit_audit_text(text: str | None, limit: int = AUDIT_TEXT_MAX_BYTES) -> str | None:
    """Truncate ``text`` to ``limit`` UTF-8 bytes, recording how much was cut."""
    if text is None:
        return None
    encoded = text.encode("utf-8", errors="surrogateescape")
    if len(encoded) <= limit:
        return text
    marker_budget = 64
    kept = encoded[: limit - marker_budget].decode("utf-8", errors="ignore")
    removed = len(encoded) - len(kept.encode("utf-8", errors="surrogateescape"))
    return f"{kept} /* [truncated {removed} bytes] */"


_COLUMNS = (
    "query_id",
    "event_type",
    "event_time",
    "user_name",
    "object_type",
    "object_name",
    "action",
    "sql_text",
    "status",
    "error_message",
    "duration_ms",
    "rows_affected",
    "session_id",
    "rewritten_sql",
    "file_id",
    "database_name",
    "schema_name",
    "active_role",
    "security_context_version",
    "decision",
    "ranger_policy_ids",
)
_EXECUTION_COLUMNS = ("nova_execution_id", "engine_query_ids", "execution_purpose")
#: Upper bound on the text one grouped INSERT carries, so it stays well under
#: the engine's packet limit even when every row holds a long statement.
_GROUP_MAX_CHARS = 4_000_000


def _insert_statement(include_execution: bool, rows: int = 1) -> str:
    names = _COLUMNS + (_EXECUTION_COLUMNS if include_execution else ())
    values = ",".join("NOW()" if name == "event_time" else "%s" for name in names)
    return (
        f"INSERT INTO NOVA_SYSTEM.AUDIT_LOG ({','.join(names)}) VALUES "
        + ",".join(f"({values})" for _ in range(rows))
    )


async def _write_row(parameters: list) -> None:
    try:
        await db.execute_system(_insert_statement(True), parameters)
    except Exception as exc:
        from asyncmy.errors import ProgrammingError

        # Older metadata schemas still accept the original audit contract.
        # Retry only a proven unknown-column rejection of optional telemetry;
        # no query or uncertain audit write is retried here.
        message = str(exc).lower()
        missing = (
            isinstance(exc, ProgrammingError)
            and ("unknown column" in message or "cannot be resolved" in message)
            and any(name in message for name in _EXECUTION_COLUMNS)
        )
        if not missing:
            raise
        await db.execute_system(_insert_statement(False), parameters[: -len(_EXECUTION_COLUMNS)])


class _AuditWriter:
    """Write audit rows that arrive together as one INSERT.

    A single-row INSERT costs the engine about 100 ms and concurrent ones do
    not overlap, which capped the whole deployment near 60 audited statements a
    second however many processes ran. Rows that arrive while a write is in
    flight now share the next one. Every caller still waits for the write that
    holds its own row, so nothing is acknowledged before it is recorded, and a
    lone row is written at once, exactly as before.
    """

    def __init__(self) -> None:
        self._states: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    async def write(self, parameters: list) -> None:
        if settings.AUDIT_GROUP_MAX_ROWS <= 1:
            await _write_row(parameters)
            return
        loop = asyncio.get_running_loop()
        state = self._states.setdefault(loop, {"pending": [], "drain": None})
        written = loop.create_future()
        state["pending"].append((parameters, written))
        if state["drain"] is None or state["drain"].done():
            # An empty context: the write serves many callers and must not
            # inherit the request state of whichever one happened to start it.
            state["drain"] = loop.create_task(self._drain(state), context=contextvars.Context())
        # Shielded: a caller that goes away must not cancel a write that also
        # carries other callers' rows.
        await asyncio.shield(written)

    async def _drain(self, state: dict) -> None:
        while state["pending"]:
            group, size = [], 0
            while state["pending"] and len(group) < settings.AUDIT_GROUP_MAX_ROWS:
                row_size = sum(len(value) for value in state["pending"][0][0] if type(value) is str)
                if group and size + row_size > _GROUP_MAX_CHARS:
                    break
                group.append(state["pending"].pop(0))
                size += row_size
            await self._write_group(group)

    @staticmethod
    async def _write_group(group: list) -> None:
        if len(group) > 1:
            try:
                await db.execute_system(
                    _insert_statement(True, len(group)),
                    [value for parameters, _ in group for value in parameters],
                )
            except Exception as exc:
                from asyncmy.errors import DataError, NotSupportedError, ProgrammingError

                if not isinstance(exc, (ProgrammingError, DataError, NotSupportedError)):
                    # Not a rejection: the write may or may not have happened,
                    # so it is not repeated. Every caller sees the failure.
                    for _, written in group:
                        if not written.done():
                            written.set_exception(exc)
                    return
                # The engine refused the statement, so nothing was written.
                # One row may be the cause, or the schema may predate the
                # execution columns; write each alone so every caller gets its
                # own outcome instead of sharing a failure.
            else:
                for _, written in group:
                    if not written.done():
                        written.set_result(None)
                return
        for parameters, written in group:
            try:
                await _write_row(parameters)
            except Exception as exc:
                if not written.done():
                    written.set_exception(exc)
            else:
                if not written.done():
                    written.set_result(None)


_audit_writer = _AuditWriter()


async def write_audit_log(
    *,
    event_type: str,
    user_name: str,
    action: str,
    object_type: str,
    object_name: str,
    status: str,
    sql_text: str | None = None,
    rewritten_sql: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    rows_affected: int | None = None,
    session_id: str | None = None,
    file_id: str | None = None,
    database_name: str | None = None,
    schema_name: str | None = None,
    active_role: str | None = None,
    security_context_version: int | None = None,
    decision: str | None = None,
    ranger_policy_ids: str | None = None,
    query_id: str | None = None,
    nova_execution_id: str | None = None,
    engine_query_ids: list[str] | None = None,
) -> str:
    """Write an audit log entry. Returns the query_id (UUID) for the entry."""
    from app.modules.query_autopilot.telemetry import EXECUTION, PURPOSE

    execution = EXECUTION.get()
    if execution is not None:
        nova_execution_id = nova_execution_id or execution.id
        engine_query_ids = engine_query_ids if engine_query_ids is not None else execution.query_ids
    qid = query_id or str(uuid.uuid4())
    sql_text = fit_audit_text(redact_sql_credentials(sql_text) if sql_text else sql_text)
    rewritten_sql = fit_audit_text(
        redact_sql_credentials(rewritten_sql) if rewritten_sql else rewritten_sql
    )
    error_message = fit_audit_text(
        redact_sql_credentials(error_message) if error_message else error_message
    )
    await _audit_writer.write(
        [
            qid,
            event_type,
            user_name,
            object_type,
            object_name,
            action,
            sql_text,
            status,
            error_message,
            duration_ms,
            rows_affected,
            session_id,
            rewritten_sql,
            file_id,
            database_name,
            schema_name,
            active_role,
            security_context_version,
            decision,
            ranger_policy_ids,
            nova_execution_id,
            json.dumps(engine_query_ids) if engine_query_ids else None,
            PURPOSE.get() if execution else None,
        ]
    )
    if execution is not None:
        execution.audit_ids.append(qid)
    return qid
