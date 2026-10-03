-- Additive Intelligence metadata. Existing-column upgrades also run through their owning runtime initializers.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_CONTEXT_NODES (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_CONTEXT_EDGES (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_MONITORS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_OBSERVATIONS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_NEWS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_INVESTIGATIONS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_DECISIONS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_DECISION_EVENTS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_DECISION_OUTCOMES (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_MEMORIES (
    memory_id VARCHAR(64) NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    fact_key VARCHAR(160) NOT NULL,
    fact TEXT NOT NULL,
    source_quote VARCHAR(512) NOT NULL,
    source_thread_id VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(memory_id)
DISTRIBUTED BY HASH(memory_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_MEMORY_REVISIONS (
    memory_id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(memory_id, revision, operation_id)
DISTRIBUTED BY HASH(memory_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_MEMORY_EVIDENCE (
    memory_id VARCHAR(64) NOT NULL,
    evidence_id VARCHAR(32) NOT NULL,
    user_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(memory_id, evidence_id)
DISTRIBUTED BY HASH(memory_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

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
    ,proposal_kind VARCHAR(32) DEFAULT 'metric'
    ,details JSON
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_BUSINESS_POLICIES (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    definition JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_MEMORY_REVISIONS
(memory_id,revision,operation_id,user_name,agent_id,role_name,payload,created_at)
SELECT m.memory_id,1,'legacy-private-statement-v1',m.user_name,m.agent_id,m.role_name,
       JSON_OBJECT('memory_id',m.memory_id,'revision',1,'fact',m.fact,
                   'state','HYPOTHESIS','visibility','PRIVATE','authority','user_statement'),
       m.created_at
FROM NOVA_SYSTEM.CONFIG_AGENT_MEMORIES m
LEFT JOIN NOVA_SYSTEM.CONFIG_AGENT_MEMORY_REVISIONS r ON m.memory_id=r.memory_id
WHERE r.memory_id IS NULL;
