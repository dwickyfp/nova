"""Flat, additive StarRocks control-plane schema; no user data is read here."""

from asyncmy.errors import ProgrammingError

from app.core.database import db

TABLES = {
    name: f"QUERY_AUTOPILOT_{name.upper()}"
    for name in (
        "families",
        "observations",
        "rollups",
        "baselines",
        "evidence",
        "incidents",
        "opportunities",
        "experiments",
        "actions",
        "outcomes",
        "policies",
        "enrollments",
        "jobs",
    )
}

DDL = tuple(
    f"""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.{table} (
    id VARCHAR(128) NOT NULL,
    family_id VARCHAR(64) NOT NULL,
    cohort_id VARCHAR(64) NOT NULL,
    version BIGINT NOT NULL,
    state VARCHAR(32) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    expires_at DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")"""
    for table in TABLES.values()
)


async def ensure_schema() -> None:
    for ddl in DDL:
        await db.execute_system(ddl)
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
