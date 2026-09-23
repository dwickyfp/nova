CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RULE_PROPOSALS (
    proposal_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    memory_id VARCHAR(64) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    metric_name VARCHAR(160) NOT NULL,
    prior_expression TEXT NOT NULL,
    proposed_expression TEXT NOT NULL,
    prior_fingerprint VARCHAR(128) NOT NULL,
    proposed_fingerprint VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    previewed_at DATETIME NULL,
    reviewed_by VARCHAR(128) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(proposal_id)
DISTRIBUTED BY HASH(proposal_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_MODEL_VERSIONS (
    version_id VARCHAR(64) NOT NULL,
    semantic_model_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    proposal_id VARCHAR(64) NOT NULL,
    model_fingerprint VARCHAR(128) NOT NULL,
    definition JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(version_id)
DISTRIBUTED BY HASH(version_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
