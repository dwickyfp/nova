-- Nova native ML metadata upgrade.
--
-- Fresh installations receive these definitions from docker/init-nova.sql.
-- Existing installations should apply this migration during a maintenance
-- window. It is additive except for the alias-table key conversion; preserve
-- the old table until the final validation query succeeds.

ALTER TABLE NOVA_SYSTEM.ML_MODELS ADD COLUMN current_version INT DEFAULT "0";
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN artifact_uri VARCHAR(2048);
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN artifact_sha256 VARCHAR(64);
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN artifact_size BIGINT;
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN task VARCHAR(64);
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN framework VARCHAR(128);
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN framework_version VARCHAR(64);
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN algorithm VARCHAR(128);
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN feature_schema TEXT;
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS ADD COLUMN training_duration_ms BIGINT;

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_VERSIONS_PK (
  model_id VARCHAR(64) NOT NULL,
  version INT NOT NULL,
  status VARCHAR(32) NOT NULL,
  training_rows BIGINT,
  metrics TEXT,
  artifact_uri VARCHAR(2048),
  artifact_sha256 VARCHAR(64),
  artifact_size BIGINT,
  task VARCHAR(64),
  framework VARCHAR(128),
  framework_version VARCHAR(64),
  algorithm VARCHAR(128),
  feature_schema TEXT,
  training_duration_ms BIGINT,
  model_binary TEXT,
  created_at DATETIME NOT NULL,
  created_by VARCHAR(128)
) PRIMARY KEY(model_id, version)
DISTRIBUTED BY HASH(model_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO NOVA_SYSTEM.ML_MODEL_VERSIONS_PK
SELECT model_id,version,status,training_rows,metrics,artifact_uri,artifact_sha256,
       artifact_size,task,framework,framework_version,algorithm,feature_schema,
       training_duration_ms,model_binary,created_at,created_by
FROM NOVA_SYSTEM.ML_MODEL_VERSIONS;

ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS RENAME ML_MODEL_VERSIONS_LEGACY;
ALTER TABLE NOVA_SYSTEM.ML_MODEL_VERSIONS_PK RENAME ML_MODEL_VERSIONS;

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODELS_PK (
  model_id VARCHAR(64) NOT NULL,
  model_type VARCHAR(64) NOT NULL,
  model_name VARCHAR(256) NOT NULL,
  target_column VARCHAR(128),
  feature_columns TEXT,
  hyperparameters TEXT,
  training_sql TEXT,
  database_name VARCHAR(128),
  schema_name VARCHAR(128),
  created_at DATETIME NOT NULL,
  created_by VARCHAR(128),
  current_version INT DEFAULT "0",
  updated_at DATETIME NOT NULL
) PRIMARY KEY(model_id)
DISTRIBUTED BY HASH(model_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO NOVA_SYSTEM.ML_MODELS_PK
SELECT m.model_id,m.model_type,m.model_name,m.target_column,m.feature_columns,
       m.hyperparameters,m.training_sql,m.database_name,m.schema_name,m.created_at,
       m.created_by,GREATEST(COALESCE(m.current_version,0),COALESCE(v.latest_version,0)),
       m.updated_at
FROM NOVA_SYSTEM.ML_MODELS m
LEFT JOIN (
  SELECT model_id,MAX(version) AS latest_version
  FROM NOVA_SYSTEM.ML_MODEL_VERSIONS
  GROUP BY model_id
) v ON m.model_id=v.model_id;

ALTER TABLE NOVA_SYSTEM.ML_MODELS RENAME ML_MODELS_LEGACY;
ALTER TABLE NOVA_SYSTEM.ML_MODELS_PK RENAME ML_MODELS;

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_ALIASES_PK (
  alias_name VARCHAR(128) NOT NULL,
  owner_name VARCHAR(128) NOT NULL,
  database_name VARCHAR(128) NOT NULL,
  model_id VARCHAR(64) NOT NULL,
  version INT NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
) PRIMARY KEY(alias_name, owner_name, database_name)
DISTRIBUTED BY HASH(alias_name) BUCKETS 2
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

-- Legacy aliases have no owner/database scope. They are retained under the
-- explicit legacy scope and can be reassigned by an owner after upgrade.
INSERT INTO NOVA_SYSTEM.ML_MODEL_ALIASES_PK
SELECT alias_name, '__legacy__', '', model_id, version, created_at, updated_at
FROM NOVA_SYSTEM.ML_MODEL_ALIASES;

ALTER TABLE NOVA_SYSTEM.ML_MODEL_ALIASES RENAME ML_MODEL_ALIASES_LEGACY;
ALTER TABLE NOVA_SYSTEM.ML_MODEL_ALIASES_PK RENAME ML_MODEL_ALIASES;

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_RUNS (
  run_id VARCHAR(64) NOT NULL,
  task VARCHAR(64) NOT NULL,
  mode VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  owner_name VARCHAR(128) NOT NULL,
  tenant_name VARCHAR(128) NOT NULL,
  database_name VARCHAR(128),
  model_id VARCHAR(64),
  model_version INT,
  artifact_uri VARCHAR(2048),
  fingerprint VARCHAR(64),
  telemetry TEXT,
  error_class VARCHAR(128),
  error_message TEXT,
  created_at DATETIME NOT NULL,
  expires_at DATETIME,
  updated_at DATETIME NOT NULL
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
