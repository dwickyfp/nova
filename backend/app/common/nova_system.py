"""NOVA_SYSTEM database initialization.

Auto-creates all schemas and tables on first startup.
All persistent state lives in StarRocks NOVA_SYSTEM — no SQLite, no PostgreSQL.
"""

from app.core.database import db

# --- Schema definitions ---

SCHEMAS = ["CONFIG", "AUDIT", "STAGE", "LINEAGE", "QUALITY", "USAGE"]

# --- Table DDL ---

TABLES = {
    "CONFIG.STAGES": """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG.STAGES (
            id                    VARCHAR(64),
            name                  VARCHAR(128) NOT NULL,
            database_name         VARCHAR(128) NOT NULL,
            schema_name           VARCHAR(128) NOT NULL,
            storage_connection    VARCHAR(128) NOT NULL,
            base_prefix           VARCHAR(512) NOT NULL,
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by            VARCHAR(128),
            PRIMARY KEY (id)
        )
        DISTRIBUTED BY HASH(id) BUCKETS 1
        PROPERTIES("replication_num" = "1", "enable_persistent_index" = "true")
    """,
    "CONFIG.PINNED_QUERIES": """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG.PINNED_QUERIES (
            id              VARCHAR(64),
            user_name       VARCHAR(128) NOT NULL,
            name            VARCHAR(256) NOT NULL,
            sql_text        TEXT NOT NULL,
            database_name   VARCHAR(128),
            schema_name     VARCHAR(128),
            is_shared       BOOLEAN DEFAULT FALSE,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id)
        )
        DISTRIBUTED BY HASH(id) BUCKETS 1
        PROPERTIES("replication_num" = "1", "enable_persistent_index" = "true")
    """,
    "CONFIG.USER_PREFERENCES": """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG.USER_PREFERENCES (
            user_name       VARCHAR(128),
            pref_key        VARCHAR(128),
            pref_value      TEXT,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_name, pref_key)
        )
        DISTRIBUTED BY HASH(user_name) BUCKETS 1
        PROPERTIES("replication_num" = "1", "enable_persistent_index" = "true")
    """,
    "AUDIT.LOG": """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT.LOG (
            log_id        VARCHAR(64),
            user_name     VARCHAR(128) NOT NULL,
            action        VARCHAR(64) NOT NULL,
            target_type   VARCHAR(64),
            target_name   VARCHAR(256),
            sql_text      TEXT,
            status        VARCHAR(16) DEFAULT 'SUCCESS',
            error_message TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (log_id)
        )
        DISTRIBUTED BY HASH(log_id) BUCKETS 1
        PROPERTIES("replication_num" = "1")
    """,
    "STAGE.FILE_MANIFEST": """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.STAGE.FILE_MANIFEST (
            manifest_id   VARCHAR(64),
            stage_id      VARCHAR(64) NOT NULL,
            file_path     VARCHAR(1024) NOT NULL,
            file_size     BIGINT,
            file_format   VARCHAR(16),
            row_count     BIGINT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (manifest_id)
        )
        DISTRIBUTED BY HASH(manifest_id) BUCKETS 1
        PROPERTIES("replication_num" = "1")
    """,
    "LINEAGE.LOAD_HISTORY": """
        CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.LINEAGE.LOAD_HISTORY (
            load_id       VARCHAR(64),
            user_name     VARCHAR(128) NOT NULL,
            source_type   VARCHAR(32) NOT NULL,
            source_path   VARCHAR(1024),
            target_table  VARCHAR(256) NOT NULL,
            row_count     BIGINT,
            status        VARCHAR(16) DEFAULT 'RUNNING',
            error_message TEXT,
            started_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at   DATETIME,
            PRIMARY KEY (load_id)
        )
        DISTRIBUTED BY HASH(load_id) BUCKETS 1
        PROPERTIES("replication_num" = "1")
    """,
}


async def init_nova_system() -> None:
    """Auto-create NOVA_SYSTEM database, all schemas, and tables.

    Called once at startup in the lifespan handler.
    Uses CREATE IF NOT EXISTS — idempotent, safe to call multiple times.
    """
    # Create database
    await db.execute_system("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")

    # Create schemas
    for schema in SCHEMAS:
        await db.execute_system(f"CREATE SCHEMA IF NOT EXISTS NOVA_SYSTEM.{schema}")

    # Create tables
    for table_name, ddl in TABLES.items():
        await db.execute_system(ddl)

    # Mark setup as not complete (for first-login detection)
    result = await db.execute_system(
        "SELECT pref_value FROM NOVA_SYSTEM.CONFIG.USER_PREFERENCES "
        "WHERE user_name = '__system__' AND pref_key = 'setup_complete'"
    )
    if not result["rows"]:
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG.USER_PREFERENCES "
            "(user_name, pref_key, pref_value, updated_at) "
            "VALUES ('__system__', 'setup_complete', 'false', NOW())"
        )


async def is_setup_complete() -> bool:
    """Check if the initial admin setup has been completed."""
    try:
        result = await db.execute_system(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG.USER_PREFERENCES "
            "WHERE user_name = '__system__' AND pref_key = 'setup_complete'"
        )
        return bool(result["rows"]) and result["rows"][0][0] == "true"
    except Exception:
        return False


async def mark_setup_complete() -> None:
    """Mark the initial setup as complete."""
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG.USER_PREFERENCES "
        "(user_name, pref_key, pref_value, updated_at) "
        "VALUES ('__system__', 'setup_complete', 'true', NOW())"
    )
