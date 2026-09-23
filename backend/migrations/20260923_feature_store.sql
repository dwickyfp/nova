-- Versioned Feature Store metadata. Physical projections live in _NOVA_FEATURES.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_FEATURE_VIEWS (
    name VARCHAR(128) NOT NULL,
    id VARCHAR(64) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    source_relation VARCHAR(512) NOT NULL,
    entity_keys JSON NOT NULL,
    event_timestamp VARCHAR(128) NOT NULL,
    feature_columns JSON NOT NULL,
    freshness_seconds INT NOT NULL,
    ttl_seconds INT NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    active_version INT,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(name) DISTRIBUTED BY HASH(name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS (
    view_name VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    relation_name VARCHAR(256) NOT NULL,
    definition JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_count BIGINT NOT NULL DEFAULT "0",
    created_at DATETIME NOT NULL,
    completed_at DATETIME
) PRIMARY KEY(view_name,version) DISTRIBUTED BY HASH(view_name,version) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_FEATURE_GROUPS (
    name VARCHAR(128) NOT NULL,
    id VARCHAR(64) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    active_version INT,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
) PRIMARY KEY(name) DISTRIBUTED BY HASH(name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS (
    group_name VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    members JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    activated_at DATETIME
) PRIMARY KEY(group_name,version) DISTRIBUTED BY HASH(group_name,version) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_FEATURE_TRAINING_SETS (
    id VARCHAR(64) NOT NULL,
    group_name VARCHAR(128) NOT NULL,
    group_version INT NOT NULL,
    label_relation VARCHAR(512) NOT NULL,
    generated_sql TEXT NOT NULL,
    lineage JSON NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_FEATURE_ML_LINKS (
    id VARCHAR(64) NOT NULL,
    model_id VARCHAR(64) NOT NULL,
    model_version INT NOT NULL,
    training_set_id VARCHAR(64) NOT NULL,
    group_name VARCHAR(128) NOT NULL,
    group_version INT NOT NULL,
    lineage JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
