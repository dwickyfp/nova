-- Nova agent/semantic intelligence additive migration.
-- Safe for existing Agent Studio data: legacy instruction fields remain and
-- compile lazily when ``compiled_instructions`` is empty.

ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN discoverable_skills JSON;
ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN compiled_instructions JSON;
ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN harness_mode VARCHAR(16);

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES (
    verified_query_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    model_fingerprint VARCHAR(64) NOT NULL,
    question TEXT NOT NULL,
    semantic_plan JSON NOT NULL,
    verified_sql TEXT NOT NULL,
    expected_result_signature VARCHAR(256),
    verified_by VARCHAR(128) NOT NULL,
    verified_at DATETIME NOT NULL,
    tags JSON,
    usage_count BIGINT NOT NULL DEFAULT "0",
    success_count BIGINT NOT NULL DEFAULT "0"
) PRIMARY KEY(verified_query_id)
DISTRIBUTED BY HASH(verified_query_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_SEMANTIC_QUERY_USAGE (
    usage_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    model_fingerprint VARCHAR(64) NOT NULL,
    metric_names JSON,
    dimension_names JSON,
    filter_shape JSON,
    time_grain VARCHAR(32),
    execution_latency_ms BIGINT,
    scan_bytes BIGINT,
    succeeded BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(usage_id)
DISTRIBUTED BY HASH(usage_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
