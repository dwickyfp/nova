"""NOVA_SYSTEM database initialization.

The init-nova.sql creates tables with flat naming:
  CONFIG_STAGES, CONFIG_USER_PREFERENCES, CONFIG_PINNED_QUERIES, AUDIT_LOG, etc.

All persistent state lives in StarRocks NOVA_SYSTEM — no SQLite, no PostgreSQL.
"""

from app.core.database import db


WORKSPACE_ENTRIES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES (
    id           VARCHAR(64) NOT NULL,
    user_name    VARCHAR(128) NOT NULL,
    parent_path  VARCHAR(1024) NOT NULL,
    name         VARCHAR(256) NOT NULL,
    entry_type   VARCHAR(32) NOT NULL,
    object_key   VARCHAR(1024),
    size_bytes   BIGINT DEFAULT "0",
    etag         VARCHAR(256),
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at   DATETIME,
    is_deleted   BOOLEAN DEFAULT "false"
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


TASK_ORCHESTRATION_DDL = (
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_TASKS (
    id             VARCHAR(64) NOT NULL,
    name           VARCHAR(256) NOT NULL,
    database_name  VARCHAR(128),
    definition     TEXT,
    schedule_kind  VARCHAR(32) NOT NULL,
    schedule_expr  VARCHAR(256),
    timezone       VARCHAR(64) NOT NULL,
    when_expr      TEXT,
    overlap_policy VARCHAR(32),
    owner_role     VARCHAR(128),
    created_by     VARCHAR(128),
    version        BIGINT DEFAULT "1",
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_TASK_EDGES (
    id          VARCHAR(64) NOT NULL,
    graph_id    VARCHAR(64) NOT NULL,
    parent_task VARCHAR(256) NOT NULL,
    child_task  VARCHAR(256) NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS (
    id           VARCHAR(64) NOT NULL,
    graph_id     VARCHAR(64) NOT NULL,
    trigger_type VARCHAR(32) NOT NULL,
    state        VARCHAR(32) NOT NULL,
    wal_marks    TEXT,
    started_at   DATETIME,
    finished_at  DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_TASK_RUNS (
    id                 VARCHAR(64) NOT NULL,
    graph_run_id       VARCHAR(64),
    task_id            VARCHAR(64),
    attempt            INT DEFAULT "1",
    state              VARCHAR(32) NOT NULL,
    delegated          BOOLEAN DEFAULT "true",
    starrocks_query_id VARCHAR(128),
    error_message      TEXT,
    started_at         DATETIME,
    finished_at        DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
)


async def init_task_orchestration() -> None:
    """Idempotently create the Phase 9 CONFIG_TASK* tables.

    Invariant: no credential-bearing column is ever defined here. If a
    credential must be referenced, store the object *name*, never its value.
    """
    for ddl in TASK_ORCHESTRATION_DDL:
        await db.execute_system(ddl)


async def init_nova_system() -> None:
    """Verify NOVA_SYSTEM exists and setup marker is present.

    The actual DDL is handled by init-nova.sql in Docker.
    This just ensures the setup_complete preference exists.
    """
    try:
        await db.execute_system(WORKSPACE_ENTRIES_DDL)
        result = await db.execute_system(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
            "WHERE user_name = '__system__' AND pref_key = 'setup_complete'"
        )
        if not result["rows"]:
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
                "(user_name, pref_key, pref_value, updated_at) "
                "VALUES ('__system__', 'setup_complete', 'false', NOW())"
            )
    except Exception:
        # Tables may not exist yet if init hasn't run
        pass


async def is_setup_complete() -> bool:
    """Check if the initial admin setup has been completed."""
    try:
        result = await db.execute_system(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
            "WHERE user_name = '__system__' AND pref_key = 'setup_complete'"
        )
        return bool(result["rows"]) and result["rows"][0][0] == "true"
    except Exception:
        return False


async def mark_setup_complete() -> None:
    """Mark the initial setup as complete."""
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name, pref_key, pref_value, updated_at) "
        "VALUES ('__system__', 'setup_complete', 'true', NOW())"
    )
