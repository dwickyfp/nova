-- Streams namespace: immutable generations and durable arbitration.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_CLAIMS (
    claim_key VARCHAR(64) NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    generation BIGINT NOT NULL,
    attempt_id VARCHAR(64) NOT NULL,
    operation_digest VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(claim_key)
DISTRIBUTED BY HASH(claim_key) BUCKETS 1
PROPERTIES ('replication_num'='1');

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STREAMS (
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
) PRIMARY KEY(catalog_name, database_name, schema_name, name, generation)
DISTRIBUTED BY HASH(catalog_name, database_name, schema_name, name) BUCKETS 1
PROPERTIES ('replication_num'='1');
