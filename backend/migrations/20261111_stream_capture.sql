-- Streams capture: registered sources, the captured-commit index and stream
-- definitions (mode).
-- Additive and idempotent; runtime initialization applies the same statements.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STREAM_SOURCES (
    source_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    table_name VARCHAR(128) NOT NULL,
    table_id BIGINT NOT NULL,
    key_columns STRING NOT NULL,
    columns_json STRING NOT NULL,
    schema_fingerprint VARCHAR(64) NOT NULL,
    journal_table VARCHAR(128) NOT NULL,
    lineage_table VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    status_reason VARCHAR(512),
    registered_by VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(source_id)
DISTRIBUTED BY HASH(source_id) BUCKETS 1
PROPERTIES ('replication_num'='1');

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS (
    source_id VARCHAR(64) NOT NULL,
    commit_seq BIGINT NOT NULL,
    commit_id VARCHAR(64) NOT NULL,
    version_before BIGINT NOT NULL,
    version_after BIGINT NOT NULL,
    row_count BIGINT NOT NULL,
    principal VARCHAR(256) NOT NULL,
    active_role VARCHAR(128),
    recorded_at DATETIME NOT NULL
) PRIMARY KEY(source_id, commit_seq)
DISTRIBUTED BY HASH(source_id) BUCKETS 1
PROPERTIES ('replication_num'='1');

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STREAM_DEFINITIONS (
    stream_id VARCHAR(64) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    created_by VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(stream_id)
DISTRIBUTED BY HASH(stream_id) BUCKETS 1
PROPERTIES ('replication_num'='1');

-- AUDIT_STREAM_CONSUMPTION_OPERATIONS.dispatch_attempt is added by runtime
-- initialization only when absent (StarRocks has no ADD COLUMN IF NOT EXISTS).
