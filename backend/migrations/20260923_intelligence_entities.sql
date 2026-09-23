CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ENTITIES (
    catalog_name VARCHAR(128) NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    schema_name VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    id VARCHAR(64) NOT NULL,
    description TEXT,
    relation_name VARCHAR(512) NOT NULL,
    key_columns JSON NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    tags JSON,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(catalog_name, database_name, schema_name, name)
DISTRIBUTED BY HASH(catalog_name, database_name, schema_name, name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
