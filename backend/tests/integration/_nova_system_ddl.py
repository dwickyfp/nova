"""Shared NOVA_SYSTEM prerequisites for integration suites.

The dev/test engine ships without ``init-nova.sql``, so ``NOVA_SYSTEM`` and its
tables may be absent. Every suite that writes to ``NOVA_SYSTEM.AUDIT_LOG``
(login, the MySQL proxy, the task reconciler) therefore has to provision it
itself; this module is that provisioning in one place, so a suite never depends
on a table some earlier suite happened to create.
"""

from __future__ import annotations

import contextlib

import asyncmy

# The dev/test engine ships without init-nova.sql, so ``NOVA_SYSTEM.AUDIT_LOG``
# is absent and any audited write fails with a 1064. This is a minimal stand-in
# with the columns ``write_audit_log`` inserts; the production table
# (partitioned, DUPLICATE KEY) lives in init-nova.sql. A Primary-Key table keeps
# it simple and satisfies the ``log_id`` key the audit writer expects to be
# generated.
AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_LOG (
    log_id        BIGINT NOT NULL AUTO_INCREMENT,
    query_id      VARCHAR(36),
    event_type    VARCHAR(64) NOT NULL,
    event_time    DATETIME NOT NULL,
    user_name     VARCHAR(128),
    ip_address    VARCHAR(45),
    object_type   VARCHAR(64),
    object_name   VARCHAR(512),
    action        VARCHAR(128),
    sql_text      TEXT,
    status        VARCHAR(32),
    error_message TEXT,
    duration_ms   BIGINT,
    rows_affected BIGINT,
    client_ip     VARCHAR(45),
    session_id    VARCHAR(64),
    rewritten_sql TEXT,
    file_id       VARCHAR(64),
    database_name VARCHAR(128),
    schema_name   VARCHAR(128),
    active_role   VARCHAR(128),
    security_context_version BIGINT,
    decision      VARCHAR(32),
    ranger_policy_ids VARCHAR(2048)
) PRIMARY KEY(log_id)
DISTRIBUTED BY HASH(log_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

AUDIT_LOG_MIGRATIONS = {
    "active_role": "VARCHAR(128)",
    "security_context_version": "BIGINT",
    "decision": "VARCHAR(32)",
    "ranger_policy_ids": "VARCHAR(2048)",
}


async def ensure_audit_log(host: str, port: int, user: str, password: str) -> None:
    """Idempotently create ``NOVA_SYSTEM.AUDIT_LOG`` on an admin connection."""
    conn = await asyncmy.connect(host=host, port=port, user=user, password=password)
    try:
        async with conn.cursor() as cur:
            with contextlib.suppress(Exception):
                await cur.execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
            await cur.execute(AUDIT_LOG_DDL)
            await cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME = 'AUDIT_LOG'"
            )
            existing = {str(row[0]).casefold() for row in await cur.fetchall()}
            for column, data_type in AUDIT_LOG_MIGRATIONS.items():
                if column.casefold() not in existing:
                    await cur.execute(
                        f"ALTER TABLE NOVA_SYSTEM.AUDIT_LOG ADD COLUMN {column} {data_type}"
                    )
    finally:
        conn.close()
