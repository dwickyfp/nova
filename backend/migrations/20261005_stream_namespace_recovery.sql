-- Streams namespace recovery: immutable operation facts for durable claim winners.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_NAMESPACE_OPERATIONS (
    operation_digest VARCHAR(64) NOT NULL,
    catalog_name VARCHAR(128) NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    schema_name VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    generation BIGINT NOT NULL,
    stream_id VARCHAR(64) NOT NULL,
    source_database VARCHAR(128) NOT NULL,
    source_name VARCHAR(128) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    owner_role VARCHAR(128) NOT NULL,
    cursor_epoch BIGINT NOT NULL,
    cursor_sequence BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(operation_digest)
DISTRIBUTED BY HASH(operation_digest) BUCKETS 1
PROPERTIES ('replication_num'='1');

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS (
    event_id VARCHAR(64) NOT NULL,
    consume_id VARCHAR(64) NOT NULL,
    stream_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    generation BIGINT NOT NULL,
    sequence_from BIGINT NOT NULL,
    sequence_to BIGINT NOT NULL,
    operation_digest VARCHAR(64) NOT NULL,
    target_database VARCHAR(256) NOT NULL,
    target_object VARCHAR(256) NOT NULL,
    engine_label VARCHAR(128) NOT NULL,
    principal VARCHAR(256) NOT NULL,
    state VARCHAR(32) NOT NULL,
    receipt JSON,
    created_at DATETIME NOT NULL
) PRIMARY KEY(event_id)
DISTRIBUTED BY HASH(event_id) BUCKETS 1
PROPERTIES ('replication_num'='1');

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTION_OPERATIONS (
    consume_id VARCHAR(64) NOT NULL,
    stream_id VARCHAR(64) NOT NULL,
    generation BIGINT NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    sequence_from BIGINT NOT NULL,
    sequence_to BIGINT NOT NULL,
    schema_version BIGINT NOT NULL,
    operation_digest VARCHAR(64) NOT NULL,
    target_database VARCHAR(256) NOT NULL,
    target_object VARCHAR(256) NOT NULL,
    engine_label VARCHAR(128) NOT NULL,
    principal VARCHAR(256) NOT NULL,
    target_load_id BIGINT,
    target_transaction_id BIGINT,
    active_role VARCHAR(128),
    security_context_version BIGINT,
    submission_digest VARCHAR(64) NOT NULL,
    cancellation_digest VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(consume_id)
DISTRIBUTED BY HASH(consume_id) BUCKETS 1
PROPERTIES ('replication_num'='1');
