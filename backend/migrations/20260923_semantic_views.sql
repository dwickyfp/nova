CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS (
    catalog_name VARCHAR(128) NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    schema_name VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    active_version INT,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(catalog_name,database_name,schema_name,name)
DISTRIBUTED BY HASH(catalog_name,database_name,schema_name,name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS (
    view_id VARCHAR(64) NOT NULL,
    version INT NOT NULL,
    definition JSON NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    validation JSON,
    created_at DATETIME NOT NULL,
    validated_at DATETIME,
    activated_at DATETIME
) PRIMARY KEY(view_id,version) DISTRIBUTED BY HASH(view_id,version) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
