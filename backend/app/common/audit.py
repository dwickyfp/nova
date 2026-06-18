"""Audit helpers for Nova user actions."""

from __future__ import annotations

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
) -> None:
    await db.execute_system(
        """
        INSERT INTO NOVA_SYSTEM.AUDIT_LOG
        (event_type, event_time, user_name, object_type, object_name, action,
         sql_text, status, error_message, duration_ms, rows_affected, session_id, rewritten_sql)
        VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
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
        ],
    )
