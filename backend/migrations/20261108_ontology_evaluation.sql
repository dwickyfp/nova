-- Ontology evaluation tables for existing deployments. Every statement is idempotent.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ONTOLOGY_EVAL_CASES (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ONTOLOGY_EVAL_RUNS (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ONTOLOGY_EVAL_RESULTS (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
