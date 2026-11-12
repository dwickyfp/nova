"""Startup schema hook for Query Autopilot.

Autopilot metadata lives in PostgreSQL (`nova_meta.query_autopilot_*`, revision
0023) and is created by the metadata migrator, never at process startup. What
remains here is the audit-log execution columns the query telemetry writes,
which belong to the audit log until it is ported.
"""

from asyncmy.errors import ProgrammingError

from app.core.database import db


async def ensure_schema() -> None:
    from app.common.nova_system import _column_exists

    for name, sql_type in (
        ("nova_execution_id", "VARCHAR(64)"),
        ("engine_query_ids", "JSON"),
        ("execution_purpose", "VARCHAR(16)"),
    ):
        if not await _column_exists("AUDIT_LOG", name):
            try:
                await db.execute_system(
                    f"ALTER TABLE NOVA_SYSTEM.AUDIT_LOG ADD COLUMN {name} {sql_type}"
                )
            except ProgrammingError:
                if not await _column_exists("AUDIT_LOG", name):
                    raise
