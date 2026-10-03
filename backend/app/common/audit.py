"""Audit helpers for Nova user actions."""

from __future__ import annotations

import json
import uuid

from app.common.sql_guard import redact_sql_credentials
from app.core.database import db


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
    sql_text = redact_sql_credentials(sql_text) if sql_text else sql_text
    rewritten_sql = redact_sql_credentials(rewritten_sql) if rewritten_sql else rewritten_sql
    error_message = redact_sql_credentials(error_message) if error_message else error_message
    columns = (
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

    def insert_statement(include_execution: bool) -> str:
        names = columns + (
            ("nova_execution_id", "engine_query_ids", "execution_purpose")
            if include_execution
            else ()
        )
        values = ["NOW()" if name == "event_time" else "%s" for name in names]
        return f"INSERT INTO NOVA_SYSTEM.AUDIT_LOG ({','.join(names)}) VALUES ({','.join(values)})"

    statement = insert_statement(True)
    parameters = [
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
    try:
        await db.execute_system(statement, parameters)
    except Exception as exc:
        from asyncmy.errors import ProgrammingError

        # Older metadata schemas still accept the original audit contract.
        # Retry only a proven unknown-column rejection of optional telemetry;
        # no query or uncertain audit write is retried here.
        message = str(exc).lower()
        missing = (
            isinstance(exc, ProgrammingError)
            and ("unknown column" in message or "cannot be resolved" in message)
            and any(
                name in message
                for name in ("nova_execution_id", "engine_query_ids", "execution_purpose")
            )
        )
        if not missing:
            raise
        legacy = insert_statement(False)
        await db.execute_system(legacy, parameters[:-3])
    if execution is not None:
        execution.audit_ids.append(qid)
    return qid
