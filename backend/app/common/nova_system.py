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

#: Immutable snapshot of a workspace file at each save. The content itself
#: lives as a separate object in the workspace bucket (see
#: ``WorkspaceService._version_object_key``); this row is metadata only, so a
#: large query never bloats StarRocks. Primary Key on ``id`` because a version
#: is written once and read by (entry, version); ``version`` is a monotonic
#: per-entry counter assigned under the same save that writes the object.
WORKSPACE_FILE_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS (
    id          VARCHAR(64) NOT NULL,
    entry_id    VARCHAR(64) NOT NULL,
    user_name   VARCHAR(128) NOT NULL,
    version     BIGINT NOT NULL,
    object_key  VARCHAR(1024) NOT NULL,
    size_bytes  BIGINT DEFAULT "0",
    etag        VARCHAR(256),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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
    schema_name    VARCHAR(128),
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
    id             VARCHAR(64) NOT NULL,
    graph_id       VARCHAR(64) NOT NULL,
    trigger_type   VARCHAR(32) NOT NULL,
    state          VARCHAR(32) NOT NULL,
    overlap_policy VARCHAR(16) NOT NULL DEFAULT 'skip',
    wal_marks      TEXT,
    started_at     DATETIME,
    heartbeat_at   DATETIME,
    finished_at    DATETIME
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

ML_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_RUNS (
    run_id VARCHAR(64) NOT NULL,
    task VARCHAR(64) NOT NULL,
    mode VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    tenant_name VARCHAR(128) NOT NULL,
    database_name VARCHAR(128),
    model_id VARCHAR(64),
    model_version INT,
    artifact_uri VARCHAR(2048),
    fingerprint VARCHAR(64),
    telemetry TEXT,
    error_class VARCHAR(128),
    error_message TEXT,
    created_at DATETIME NOT NULL,
    expires_at DATETIME,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

ML_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("ML_MODELS", "current_version", 'INT DEFAULT "0"'),
    ("ML_MODEL_VERSIONS", "artifact_uri", "VARCHAR(2048)"),
    ("ML_MODEL_VERSIONS", "artifact_sha256", "VARCHAR(64)"),
    ("ML_MODEL_VERSIONS", "artifact_size", "BIGINT"),
    ("ML_MODEL_VERSIONS", "task", "VARCHAR(64)"),
    ("ML_MODEL_VERSIONS", "framework", "VARCHAR(128)"),
    ("ML_MODEL_VERSIONS", "framework_version", "VARCHAR(64)"),
    ("ML_MODEL_VERSIONS", "algorithm", "VARCHAR(128)"),
    ("ML_MODEL_VERSIONS", "feature_schema", "TEXT"),
    ("ML_MODEL_VERSIONS", "training_duration_ms", "BIGINT"),
)

ML_ALIAS_PK_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_ALIASES_PK (
    alias_name VARCHAR(128) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    model_id VARCHAR(64) NOT NULL,
    version INT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(alias_name, owner_name, database_name)
DISTRIBUTED BY HASH(alias_name) BUCKETS 2
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

ML_MODELS_PK_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODELS_PK (
    model_id VARCHAR(64) NOT NULL,
    model_type VARCHAR(64) NOT NULL,
    model_name VARCHAR(256) NOT NULL,
    target_column VARCHAR(128),
    feature_columns TEXT,
    hyperparameters TEXT,
    training_sql TEXT,
    database_name VARCHAR(128),
    schema_name VARCHAR(128),
    created_at DATETIME NOT NULL,
    created_by VARCHAR(128),
    current_version INT DEFAULT "0",
    updated_at DATETIME NOT NULL
) PRIMARY KEY(model_id)
DISTRIBUTED BY HASH(model_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

ML_MODEL_VERSIONS_PK_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_VERSIONS_PK (
    model_id VARCHAR(64) NOT NULL,
    version INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    training_rows BIGINT,
    metrics TEXT,
    artifact_uri VARCHAR(2048),
    artifact_sha256 VARCHAR(64),
    artifact_size BIGINT,
    task VARCHAR(64),
    framework VARCHAR(128),
    framework_version VARCHAR(64),
    algorithm VARCHAR(128),
    feature_schema TEXT,
    training_duration_ms BIGINT,
    model_binary TEXT,
    created_at DATETIME NOT NULL,
    created_by VARCHAR(128)
) PRIMARY KEY(model_id, version)
DISTRIBUTED BY HASH(model_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

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
    # Tasks are scoped to a database.schema like a stage (CONFIG_STAGES). The
    # column was added after the table shipped, so an existing install needs the
    # additive migration; existing rows default to NULL and are treated as the
    # legacy flat-``name`` scope until re-created.
    ("CONFIG_TASKS", "schema_name", "VARCHAR(128)"),
    # NOVA-54 / 9b: distinguishes a normal dependency edge (``after``) from a
    # ``FINALIZE`` edge. The finalizer must be stored, not dropped: its run
    # semantics are wired in PR 3b, and losing the flag here would make a
    # finalizer indistinguishable from an ordinary dependency. Existing rows
    # default to ``after``, which is the behaviour they already had.
    ("CONFIG_TASK_EDGES", "edge_kind", "VARCHAR(16) NOT NULL DEFAULT 'after'"),
    # NOVA-54 / 9b stage 3b: the overlap policy is copied onto the graph run at
    # enqueue so the worker can honour QUEUE (defer while another run for the
    # same graph is active) versus ALLOW (proceed concurrently). Existing rows
    # default to ``skip``, the strictest policy, so nothing starts overlapping
    # behaviour simply because the column arrived.
    ("CONFIG_TASK_GRAPH_RUNS", "overlap_policy", "VARCHAR(16) NOT NULL DEFAULT 'skip'"),
)


async def _column_exists(table: str, column: str) -> bool:
    result = await db.execute_system(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        "WHERE TABLE_SCHEMA = 'NOVA_SYSTEM' AND TABLE_NAME = %s "
        "AND COLUMN_NAME = %s",
        [table, column],
    )
    return bool(result["rows"])


async def _is_primary_key_table(table: str) -> bool:
    result = await db.execute_system(f"SHOW CREATE TABLE NOVA_SYSTEM.{table}")
    return bool(result["rows"]) and "PRIMARY KEY" in str(result["rows"][0]).upper()


async def migrate_task_orchestration_columns() -> None:
    """Add late-arriving columns to an existing ``CONFIG_TASK*`` table."""
    for table, column, column_type in TASK_ORCHESTRATION_COLUMN_MIGRATIONS:
        if await _column_exists(table, column):
            continue
        await db.execute_system(
            f"ALTER TABLE NOVA_SYSTEM.{table} ADD COLUMN {column} {column_type}"
        )


async def migrate_ml_metadata() -> None:
    """Upgrade every mutable ML registry table to its Primary Key shape."""
    await db.execute_system(ML_RUNS_DDL)
    for table, column, column_type in ML_COLUMN_MIGRATIONS:
        if not await _column_exists(table, column):
            await db.execute_system(
                f"ALTER TABLE NOVA_SYSTEM.{table} ADD COLUMN {column} {column_type}"
            )
    if not await _is_primary_key_table("ML_MODEL_VERSIONS"):
        await db.execute_system(ML_MODEL_VERSIONS_PK_DDL)
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.ML_MODEL_VERSIONS_PK "
            "SELECT model_id,version,status,training_rows,metrics,artifact_uri,"
            "artifact_sha256,artifact_size,task,framework,framework_version,algorithm,"
            "feature_schema,training_duration_ms,model_binary,created_at,created_by "
            "FROM NOVA_SYSTEM.ML_MODEL_VERSIONS"
        )
        await db.execute_system(
            "ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS "
            "RENAME ML_MODEL_VERSIONS_LEGACY"
        )
        await db.execute_system(
            "ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS_PK RENAME ML_MODEL_VERSIONS"
        )
    if not await _is_primary_key_table("ML_MODELS"):
        await db.execute_system(ML_MODELS_PK_DDL)
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.ML_MODELS_PK "
            "SELECT m.model_id,m.model_type,m.model_name,m.target_column,m.feature_columns,"
            "m.hyperparameters,m.training_sql,m.database_name,m.schema_name,m.created_at,"
            "m.created_by,GREATEST(COALESCE(m.current_version,0),"
            "COALESCE(v.latest_version,0)),m.updated_at FROM NOVA_SYSTEM.ML_MODELS m "
            "LEFT JOIN (SELECT model_id,MAX(version) AS latest_version "
            "FROM NOVA_SYSTEM.ML_MODEL_VERSIONS GROUP BY model_id) v "
            "ON m.model_id=v.model_id"
        )
        await db.execute_system(
            "ALTER TABLE NOVA_SYSTEM.ML_MODELS RENAME ML_MODELS_LEGACY"
        )
        await db.execute_system(
            "ALTER TABLE NOVA_SYSTEM.ML_MODELS_PK RENAME ML_MODELS"
        )
    if await _column_exists("ML_MODEL_ALIASES", "owner_name"):
        return
    await db.execute_system(ML_ALIAS_PK_DDL)
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.ML_MODEL_ALIASES_PK "
        "SELECT alias_name, '__legacy__', '', model_id, version, created_at, updated_at "
        "FROM NOVA_SYSTEM.ML_MODEL_ALIASES"
    )
    await db.execute_system(
        "ALTER TABLE NOVA_SYSTEM.ML_MODEL_ALIASES RENAME ML_MODEL_ALIASES_LEGACY"
    )
    await db.execute_system(
        "ALTER TABLE NOVA_SYSTEM.ML_MODEL_ALIASES_PK RENAME ML_MODEL_ALIASES"
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
        await db.execute_system(WORKSPACE_FILE_VERSIONS_DDL)
        await migrate_ml_metadata()
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
