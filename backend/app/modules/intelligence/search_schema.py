"""StarRocks metadata schema for versioned Nova AI Search indexes."""

from app.core.database import db

SEARCH_DDL = (
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES (
    name VARCHAR(128) NOT NULL,
    id VARCHAR(64) NOT NULL,
    source_relation VARCHAR(512) NOT NULL,
    key_columns JSON NOT NULL,
    content_columns JSON NOT NULL,
    filter_columns JSON NOT NULL,
    entity_id VARCHAR(64),
    model_alias VARCHAR(128),
    owner_name VARCHAR(128) NOT NULL,
    active_version INT,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(name) DISTRIBUTED BY HASH(name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS (
    index_name VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    index_id VARCHAR(64) NOT NULL,
    model_id VARCHAR(64),
    model_revision VARCHAR(128),
    dimensions INT NOT NULL,
    metric VARCHAR(32) NOT NULL,
    build_status VARCHAR(32) NOT NULL,
    indexed_rows BIGINT NOT NULL DEFAULT "0",
    failure_code VARCHAR(64),
    created_at DATETIME NOT NULL,
    completed_at DATETIME
) PRIMARY KEY(index_name, version)
DISTRIBUTED BY HASH(index_name, version) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE (
    index_name VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    source_rows BIGINT NOT NULL DEFAULT "0",
    indexed_rows BIGINT NOT NULL DEFAULT "0",
    status VARCHAR(32) NOT NULL,
    last_watermark VARCHAR(128),
    updated_at DATETIME NOT NULL
) PRIMARY KEY(index_name, version)
DISTRIBUTED BY HASH(index_name, version) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AI_SEARCH_QUERY_LOG (
    id VARCHAR(64) NOT NULL,
    index_name VARCHAR(128) NOT NULL,
    index_version INT NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    elapsed_ms BIGINT NOT NULL,
    lexical_candidates INT NOT NULL,
    semantic_candidates INT NOT NULL,
    result_count INT NOT NULL,
    status VARCHAR(16) NOT NULL,
    created_at DATETIME NOT NULL
) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1")
""",
    """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AI_SEARCH_EVAL_RUNS (
    id VARCHAR(64) NOT NULL,
    index_name VARCHAR(128) NOT NULL,
    index_version INT NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    query_count INT NOT NULL,
    metrics JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
""",
)


async def ensure_search_schema() -> None:
    for ddl in SEARCH_DDL:
        await db.execute_system(ddl)
