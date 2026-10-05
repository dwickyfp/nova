-- Shared News editions over Semantic Views.
-- Run each ALTER once. The application applies the same columns at startup and
-- tolerates a column that already exists.
ALTER TABLE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS ADD COLUMN news_enabled BOOLEAN;
ALTER TABLE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS ADD COLUMN news_config JSON;
ALTER TABLE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS ADD COLUMN news_updated_by VARCHAR(128);
ALTER TABLE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS ADD COLUMN news_updated_at DATETIME;

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_EDITIONS (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_STORIES (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
