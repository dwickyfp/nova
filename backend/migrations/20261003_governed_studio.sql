-- Governed Studio metadata. Runtime initializers also apply additive column upgrades.

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RELEASE_MANIFESTS (
    manifest_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    version_id VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(manifest_id)
DISTRIBUTED BY HASH(manifest_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_QUALITY_CASES (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_QUALITY_RUNS (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_QUALITY_MONITORING (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_IMPROVEMENT_PROPOSALS (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_MISSIONS (
    mission_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    revision BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(mission_id)
DISTRIBUTED BY HASH(mission_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_DELIVERABLES (
    deliverable_id VARCHAR(64) NOT NULL,
    mission_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    request_digest VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(deliverable_id)
DISTRIBUTED BY HASH(deliverable_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_RESOURCES (
    resource_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    root_run_id VARCHAR(64) NOT NULL,
    message_id VARCHAR(64) NOT NULL,
    attachment_index INT NOT NULL,
    payload JSON NOT NULL
) PRIMARY KEY(resource_id)
DISTRIBUTED BY HASH(resource_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_RESOURCE_GRANTS (
    resource_id VARCHAR(64) NOT NULL,
    participant_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    root_run_id VARCHAR(64) NOT NULL,
    grantor_participant_id VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(resource_id,participant_id)
DISTRIBUTED BY HASH(resource_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_ACTIONS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_ACTION_EVENTS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_INTELLIGENCE_COMPARISONS (
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENTS (
    agent_id                   VARCHAR(64) NOT NULL,
    owner_name                 VARCHAR(128) NOT NULL,
    database_name              VARCHAR(128),
    schema_name                VARCHAR(128),
    name                       VARCHAR(128) NOT NULL,
    description                TEXT,
    avatar                     VARCHAR(256),
    color                      VARCHAR(32),
    model_provider_id          VARCHAR(64),
    model_name                 VARCHAR(128),
    instructions_response      TEXT,
    instructions_orchestration TEXT,
    response_style             VARCHAR(64),
    sample_questions           JSON,
    budget_seconds             INT,
    budget_tokens              INT,
    tool_not_accessible        VARCHAR(16),
    default_tools              JSON,
    default_skills             JSON,
    discoverable_skills        JSON,
    compiled_instructions      JSON,
    harness_mode               VARCHAR(16),
    policy                     VARCHAR(32),
    semantic_model_id          VARCHAR(64),
    semantic_model_ids         JSON,
    semantic_view_ids          JSON,
    visibility                 VARCHAR(16),
    created_at                 DATETIME NOT NULL,
    updated_at                 DATETIME NOT NULL,
    resource_bindings          JSON,
    config_revision            VARCHAR(64),
    budget_profile             VARCHAR(16),
    release_manifest_id        VARCHAR(64)
) PRIMARY KEY(agent_id)
DISTRIBUTED BY HASH(agent_id) BUCKETS 1
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
    created_at DATETIME NOT NULL,
    active_role VARCHAR(128),
    security_context_version BIGINT,
    semantic_version BIGINT
) PRIMARY KEY(usage_id)
DISTRIBUTED BY HASH(usage_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RUNS (
    run_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    last_sequence BIGINT NOT NULL,
    started_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    session_id VARCHAR(64),
    security_version INT
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS (
    run_id VARCHAR(64) NOT NULL,
    sequence BIGINT NOT NULL,
    frame TEXT NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(run_id, sequence)
DISTRIBUTED BY HASH(run_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
