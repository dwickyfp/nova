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
    consecutive_fail_count INT DEFAULT "0",
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
    edge_kind   VARCHAR(16) NOT NULL DEFAULT 'after',
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
    heartbeat_at DATETIME,
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
    heartbeat_at       DATETIME,
    finished_at        DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
)

#: Additive migrations for tables that shipped without a heartbeat column.
#: ``CREATE TABLE IF NOT EXISTS`` cannot evolve an existing table, and the
#: heartbeat is what lets a restarted worker tell a live run from an abandoned
#: one (acceptance criterion 7), so it is added explicitly.
#:
#: StarRocks rejects ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` (verified on
#: 4.1.1: "No viable statement for input 'ADD COLUMN IF'"), so idempotency is
#: the caller's job: each entry is ``(table, column, type)`` and the migration
#: is applied only when ``information_schema.columns`` says it is absent.
TASK_ORCHESTRATION_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("CONFIG_TASK_RUNS", "heartbeat_at", "DATETIME"),
    ("CONFIG_TASK_GRAPH_RUNS", "heartbeat_at", "DATETIME"),
    # Nova's own consecutive-failure counter. The engine auto-pauses a task
    # after ``max_task_consecutive_fail_count`` consecutive failures but does
    # not expose the count, so Nova keeps its own to detect the threshold
    # (NOVA-37 AC #3). Reset to 0 on a successful run.
    ("CONFIG_TASKS", "consecutive_fail_count", "INT DEFAULT \"0\""),
    # NOVA-54 / 9b: distinguishes a normal dependency edge (``after``) from a
    # ``FINALIZE`` edge. The finalizer must be stored, not dropped: its run
    # semantics are wired in PR 3b, and losing the flag here would make a
    # finalizer indistinguishable from an ordinary dependency. Existing rows
    # default to ``after``, which is the behaviour they already had.
    ("CONFIG_TASK_EDGES", "edge_kind", "VARCHAR(16) NOT NULL DEFAULT 'after'"),
)


async def _column_exists(table: str, column: str) -> bool:
    result = await db.execute_system(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME = %s "
        "AND COLUMN_NAME = %s",
        [table, column],
    )
    return bool(result["rows"])


async def migrate_task_orchestration_columns() -> None:
    """Add late-arriving columns to an existing ``CONFIG_TASK*`` table."""
    for table, column, column_type in TASK_ORCHESTRATION_COLUMN_MIGRATIONS:
        if await _column_exists(table, column):
            continue
        await db.execute_system(
            f"ALTER TABLE NOVA_SYSTEM.{table} ADD COLUMN {column} {column_type}"
        )


async def init_task_orchestration() -> None:
    """Idempotently create the Phase 9 CONFIG_TASK* tables.

    Invariant: no credential-bearing column is ever defined here. If a
    credential must be referenced, store the object *name*, never its value.
    """
    for ddl in TASK_ORCHESTRATION_DDL:
        await db.execute_system(ddl)
    await migrate_task_orchestration_columns()


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
