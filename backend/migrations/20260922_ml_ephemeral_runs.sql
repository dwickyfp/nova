-- Additive upgrade; existing models, versions, aliases, and run history remain intact.
ALTER TABLE NOVA_SYSTEM.ML_MODELS
ADD COLUMN tenant_name VARCHAR(128) NOT NULL DEFAULT "default";

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_EPHEMERAL_RUNS (
    run_id VARCHAR(64) NOT NULL,
    scope_key VARCHAR(2048) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    descriptor_json STRING NOT NULL,
    expires_epoch DOUBLE NOT NULL
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
