"""Audit helpers for Nova user actions."""

from __future__ import annotations

import uuid

from app.core.database import db
from app.common.sql_guard import redact_sql_credentials


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
) -> str:
    """Write an audit log entry. Returns the query_id (UUID) for the entry."""
    qid = query_id or str(uuid.uuid4())
    sql_text = redact_sql_credentials(sql_text) if sql_text else sql_text
    rewritten_sql = redact_sql_credentials(rewritten_sql) if rewritten_sql else rewritten_sql
    error_message = redact_sql_credentials(error_message) if error_message else error_message
    await db.execute_system(
        """
        INSERT INTO NOVA_SYSTEM.AUDIT_LOG
        (query_id, event_type, event_time, user_name, object_type, object_name, action,
         sql_text, status, error_message, duration_ms, rows_affected, session_id,
         rewritten_sql, file_id, database_name, schema_name, active_role,
         security_context_version, decision, ranger_policy_ids)
        VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s)
        """,
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
        ],
    )
    return qid
