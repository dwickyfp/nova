-- =============================================
-- Nova StarRocks Init Script
-- Runs once on first startup via starrocks-init container
-- =============================================

-- ── Global Session Variables ──
SET GLOBAL time_zone = 'Asia/Jakarta';
SET GLOBAL enable_pipeline_engine = true;
SET GLOBAL parallel_fragment_exec_instance_num = 1;
SET GLOBAL enable_profile = false;
SET GLOBAL query_timeout = 300;
SET GLOBAL exec_mem_limit = 2147483648;
SET GLOBAL enable_spill = true;
SET GLOBAL spill_mode = "auto";
SET GLOBAL enable_materialized_view_rewrite = true;
SET GLOBAL activate_all_roles_on_login = true;
SET GLOBAL query_queue_concurrency_limit = 100;
SET GLOBAL query_queue_max_queued_queries = 1000;
SET GLOBAL query_queue_pending_timeout_second = 300;

-- ── Create ACCOUNTADMIN role (IMMUTABLE — never drop/revoke) ──
-- This is the SUPER USER role — maximum privileges, cannot be restricted
CREATE ROLE IF NOT EXISTS ACCOUNTADMIN;

-- Object-level privileges (ALL databases, tables, views, etc.)
GRANT ALL ON CATALOG default_catalog TO ROLE ACCOUNTADMIN WITH GRANT OPTION;
GRANT ALL ON ALL DATABASES TO ROLE ACCOUNTADMIN WITH GRANT OPTION;
GRANT ALL ON ALL TABLES IN ALL DATABASES TO ROLE ACCOUNTADMIN WITH GRANT OPTION;
GRANT ALL ON ALL VIEWS IN ALL DATABASES TO ROLE ACCOUNTADMIN WITH GRANT OPTION;
GRANT ALL ON ALL MATERIALIZED VIEWS IN ALL DATABASES TO ROLE ACCOUNTADMIN WITH GRANT OPTION;
GRANT ALL ON ALL FUNCTIONS IN ALL DATABASES TO ROLE ACCOUNTADMIN WITH GRANT OPTION;

-- System-level privileges (explicit — NOT covered by ALL ON *.*)
GRANT OPERATE ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT CREATE RESOURCE GROUP ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT CREATE RESOURCE ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT CREATE EXTERNAL CATALOG ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT REPOSITORY ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT CREATE STORAGE VOLUME ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT BLACKLIST ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT FILE ON SYSTEM TO ROLE ACCOUNTADMIN;
GRANT SECURITY ON SYSTEM TO ROLE ACCOUNTADMIN;

-- ── Create nova_admin user ──
-- Default password: nova (must be changed on first login)
CREATE USER IF NOT EXISTS 'nova_admin' IDENTIFIED BY 'nova';
GRANT ACCOUNTADMIN TO USER 'nova_admin'@'%';

-- ── Create NOVA_SYSTEM database ──
CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM;

-- ═══════════════════════════════════════
-- StarRocks has catalog.database.table namespaces (no nested schemas).
-- Nova keeps all state in the single NOVA_SYSTEM database and uses a
-- <group>_<table> naming convention to preserve logical grouping.
--
-- CONFIG group (Primary Key tables — CRUD)
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STAGES (
  id                  VARCHAR(64) NOT NULL,
  name                VARCHAR(128) NOT NULL,
  database_name       VARCHAR(128) NOT NULL,
  schema_name         VARCHAR(128) NOT NULL,
  storage_connection  VARCHAR(128) NOT NULL,
  base_prefix         VARCHAR(512) NOT NULL,
  created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by          VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_PINNED_QUERIES (
  id            VARCHAR(64) NOT NULL,
  user_name     VARCHAR(128) NOT NULL,
  name          VARCHAR(256) NOT NULL,
  sql_text      TEXT NOT NULL,
  database_name VARCHAR(128),
  schema_name   VARCHAR(128),
  is_shared     BOOLEAN DEFAULT "false",
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_USER_PREFERENCES (
  user_name  VARCHAR(128) NOT NULL,
  pref_key   VARCHAR(128) NOT NULL,
  pref_value TEXT,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY (user_name, pref_key)
DISTRIBUTED BY HASH(user_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AI_PROVIDERS (
  id             VARCHAR(64) NOT NULL,
  name           VARCHAR(128) NOT NULL,
  type           VARCHAR(32) NOT NULL,
  endpoint       VARCHAR(512) NOT NULL,
  api_key_env    VARCHAR(128),
  default_params TEXT,
  is_active      BOOLEAN DEFAULT "true",
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by     VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AI_MODELS (
  id             VARCHAR(64) NOT NULL,
  provider_id    VARCHAR(64) NOT NULL,
  name           VARCHAR(128) NOT NULL,
  display_name   VARCHAR(256),
  type           VARCHAR(32) NOT NULL,
  max_tokens     INT DEFAULT "4096",
  default_params TEXT,
  is_active      BOOLEAN DEFAULT "true",
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by     VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_OBJECT_TAGS (
  object_type VARCHAR(32) NOT NULL,
  object_name VARCHAR(512) NOT NULL,
  tag_key     VARCHAR(128) NOT NULL,
  tag_value   VARCHAR(512),
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by  VARCHAR(128)
) PRIMARY KEY (object_type, object_name, tag_key)
DISTRIBUTED BY HASH(object_type) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_DASHBOARDS (
  id          VARCHAR(64) NOT NULL,
  name        VARCHAR(256) NOT NULL,
  description TEXT,
  is_shared   BOOLEAN DEFAULT "false",
  created_by  VARCHAR(128),
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_DASHBOARD_WIDGETS (
  id              VARCHAR(64) NOT NULL,
  dashboard_id    VARCHAR(64),
  title           VARCHAR(256),
  sql_text        TEXT,
  chart_type      VARCHAR(32),
  x_axis          VARCHAR(64),
  y_axis          VARCHAR(64),
  position_x      INT,
  position_y      INT,
  width           INT DEFAULT "4",
  height          INT DEFAULT "3",
  refresh_seconds INT DEFAULT "300",
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

-- ═══════════════════════════════════════
-- ML Schema (Duplicate tables for analytics)
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODELS (
  model_id    VARCHAR(64) NOT NULL,
  model_type  VARCHAR(64) NOT NULL,
  model_name  VARCHAR(256) NOT NULL,
  target_column VARCHAR(128),
  feature_columns TEXT,
  hyperparameters TEXT,
  training_sql TEXT,
  database_name VARCHAR(128),
  schema_name   VARCHAR(128),
  created_at  DATETIME NOT NULL,
  created_by  VARCHAR(128),
  updated_at  DATETIME NOT NULL
) DUPLICATE KEY(model_id, model_type)
DISTRIBUTED BY HASH(model_id) BUCKETS 4
PROPERTIES("replication_num"="1");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_VERSIONS (
  model_id     VARCHAR(64) NOT NULL,
  version      INT NOT NULL,
  status       VARCHAR(32) NOT NULL,
  training_rows BIGINT,
  metrics      TEXT,
  model_binary TEXT,
  created_at   DATETIME NOT NULL,
  created_by   VARCHAR(128)
) DUPLICATE KEY(model_id, version)
DISTRIBUTED BY HASH(model_id) BUCKETS 4
PROPERTIES("replication_num"="1");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_MODEL_ALIASES (
  alias_name  VARCHAR(128) NOT NULL,
  model_id    VARCHAR(64) NOT NULL,
  version     INT NOT NULL,
  created_at  DATETIME NOT NULL,
  updated_at  DATETIME NOT NULL
) DUPLICATE KEY(alias_name, model_id)
DISTRIBUTED BY HASH(alias_name) BUCKETS 2
PROPERTIES("replication_num"="1");

-- ═══════════════════════════════════════
-- AUDIT Schema
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_LOG (
  log_id        BIGINT NOT NULL AUTO_INCREMENT,
  event_type    VARCHAR(64) NOT NULL,
  event_time    DATETIME NOT NULL,
  user_name     VARCHAR(128),
  ip_address    VARCHAR(45),
  object_type   VARCHAR(64),
  object_name   VARCHAR(512),
  action        VARCHAR(128),
  sql_text      TEXT,
  status        VARCHAR(32),
  error_message TEXT,
  duration_ms   BIGINT,
  rows_affected BIGINT,
  client_ip     VARCHAR(45),
  session_id    VARCHAR(64),
  rewritten_sql TEXT
) DUPLICATE KEY(log_id, event_type, event_time)
PARTITION BY RANGE(event_time) (
  PARTITION p202601 VALUES LESS THAN ("2026-02-01"),
  PARTITION p202602 VALUES LESS THAN ("2026-03-01"),
  PARTITION p202603 VALUES LESS THAN ("2026-04-01"),
  PARTITION p202604 VALUES LESS THAN ("2026-05-01"),
  PARTITION p202605 VALUES LESS THAN ("2026-06-01"),
  PARTITION p202606 VALUES LESS THAN ("2026-07-01"),
  PARTITION p202607 VALUES LESS THAN ("2026-08-01"),
  PARTITION p202608 VALUES LESS THAN ("2026-09-01"),
  PARTITION p202609 VALUES LESS THAN ("2026-10-01"),
  PARTITION p202610 VALUES LESS THAN ("2026-11-01"),
  PARTITION p202611 VALUES LESS THAN ("2026-12-01"),
  PARTITION p202612 VALUES LESS THAN ("2027-01-01")
)
DISTRIBUTED BY HASH(log_id) BUCKETS 8
PROPERTIES("replication_num"="1");

-- ═══════════════════════════════════════
-- STAGE Schema
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.STAGE_FILE_MANIFEST (
  file_id      BIGINT NOT NULL AUTO_INCREMENT,
  stage_id     VARCHAR(64) NOT NULL,
  file_path    VARCHAR(1024) NOT NULL,
  file_name    VARCHAR(256) NOT NULL,
  file_size    BIGINT,
  file_format  VARCHAR(32),
  uploaded_at  DATETIME NOT NULL,
  uploaded_by  VARCHAR(128),
  checksum     VARCHAR(128)
) DUPLICATE KEY(file_id, stage_id)
DISTRIBUTED BY HASH(stage_id) BUCKETS 4
PROPERTIES("replication_num"="1");

-- ═══════════════════════════════════════
-- LINEAGE Schema
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.LINEAGE_LOAD_HISTORY (
  load_id      BIGINT NOT NULL AUTO_INCREMENT,
  target_table VARCHAR(512) NOT NULL,
  started_at   DATETIME NOT NULL,
  stage_id     VARCHAR(64),
  file_path    VARCHAR(1024),
  file_format  VARCHAR(32),
  rows_loaded  BIGINT,
  rows_rejected BIGINT,
  load_time_ms BIGINT,
  status       VARCHAR(32),
  error_message TEXT,
  completed_at DATETIME
) DUPLICATE KEY(load_id, target_table, started_at)
PARTITION BY RANGE(started_at) (
  PARTITION p202601 VALUES LESS THAN ("2026-02-01"),
  PARTITION p202602 VALUES LESS THAN ("2026-03-01"),
  PARTITION p202603 VALUES LESS THAN ("2026-04-01"),
  PARTITION p202604 VALUES LESS THAN ("2026-05-01"),
  PARTITION p202605 VALUES LESS THAN ("2026-06-01"),
  PARTITION p202606 VALUES LESS THAN ("2026-07-01"),
  PARTITION p202607 VALUES LESS THAN ("2026-08-01"),
  PARTITION p202608 VALUES LESS THAN ("2026-09-01"),
  PARTITION p202609 VALUES LESS THAN ("2026-10-01"),
  PARTITION p202610 VALUES LESS THAN ("2026-11-01"),
  PARTITION p202611 VALUES LESS THAN ("2026-12-01"),
  PARTITION p202612 VALUES LESS THAN ("2027-01-01")
)
DISTRIBUTED BY HASH(load_id) BUCKETS 4
PROPERTIES("replication_num"="1");

-- ═══════════════════════════════════════
-- QUALITY Schema
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.QUALITY_TABLE_STATS (
  stat_id       BIGINT NOT NULL AUTO_INCREMENT,
  table_name    VARCHAR(512) NOT NULL,
  database_name VARCHAR(128),
  schema_name   VARCHAR(128),
  row_count     BIGINT,
  data_size_bytes BIGINT,
  index_size_bytes BIGINT,
  partition_count INT,
  collected_at  DATETIME NOT NULL
) DUPLICATE KEY(stat_id, table_name)
DISTRIBUTED BY HASH(stat_id) BUCKETS 4
PROPERTIES("replication_num"="1");

-- ═══════════════════════════════════════
-- USAGE Schema
-- ═══════════════════════════════════════

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.USAGE_QUERY_STATS (
  stat_id         BIGINT NOT NULL AUTO_INCREMENT,
  user_name       VARCHAR(128),
  started_at      DATETIME NOT NULL,
  query_id        VARCHAR(128),
  database_name   VARCHAR(128),
  sql_text        TEXT,
  query_type      VARCHAR(32),
  execution_time_ms BIGINT,
  rows_scanned    BIGINT,
  rows_returned   BIGINT,
  memory_used_bytes BIGINT,
  cpu_time_ms     BIGINT,
  spill_bytes     BIGINT,
  completed_at    DATETIME
) DUPLICATE KEY(stat_id, user_name, started_at)
PARTITION BY RANGE(started_at) (
  PARTITION p202601 VALUES LESS THAN ("2026-02-01"),
  PARTITION p202602 VALUES LESS THAN ("2026-03-01"),
  PARTITION p202603 VALUES LESS THAN ("2026-04-01"),
  PARTITION p202604 VALUES LESS THAN ("2026-05-01"),
  PARTITION p202605 VALUES LESS THAN ("2026-06-01"),
  PARTITION p202606 VALUES LESS THAN ("2026-07-01"),
  PARTITION p202607 VALUES LESS THAN ("2026-08-01"),
  PARTITION p202608 VALUES LESS THAN ("2026-09-01"),
  PARTITION p202609 VALUES LESS THAN ("2026-10-01"),
  PARTITION p202610 VALUES LESS THAN ("2026-11-01"),
  PARTITION p202611 VALUES LESS THAN ("2026-12-01"),
  PARTITION p202612 VALUES LESS THAN ("2027-01-01")
)
DISTRIBUTED BY HASH(stat_id) BUCKETS 8
PROPERTIES("replication_num"="1");

SELECT 'Nova init complete! All tables created.' AS status;
