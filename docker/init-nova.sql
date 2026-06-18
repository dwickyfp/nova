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

-- ═══════════════════════════════════════════════════════════════
-- NOVA_DEMO — E-Commerce Sample Database
-- ═══════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS NOVA_DEMO;

-- ── Customers ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_DEMO.customers (
  customer_id   BIGINT        NOT NULL,
  first_name    VARCHAR(50)   NOT NULL,
  last_name     VARCHAR(50)   NOT NULL,
  email         VARCHAR(120)  NOT NULL,
  phone         VARCHAR(20),
  city          VARCHAR(80),
  country       VARCHAR(60)   DEFAULT 'Indonesia',
  created_at    DATETIME      NOT NULL
) PRIMARY KEY(customer_id)
DISTRIBUTED BY HASH(customer_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_DEMO.customers VALUES
(1, 'Dwicky', 'Putra', 'dwicky@example.com', '081234567890', 'Jakarta', 'Indonesia', '2025-01-15 10:00:00'),
(2, 'Kezia', 'Tan', 'kezia@example.com', '081298765432', 'Surabaya', 'Indonesia', '2025-02-20 14:30:00'),
(3, 'Budi', 'Santoso', 'budi@example.com', '085612345678', 'Bandung', 'Indonesia', '2025-03-10 09:15:00'),
(4, 'Sarah', 'Kim', 'sarah@example.com', '087812345678', 'Semarang', 'Indonesia', '2025-04-05 16:45:00'),
(5, 'Rizki', 'Pratama', 'rizki@example.com', '089912345678', 'Yogyakarta', 'Indonesia', '2025-05-12 11:20:00'),
(6, 'Maya', 'Wijaya', 'maya@example.com', '081112223344', 'Medan', 'Indonesia', '2025-06-18 08:00:00'),
(7, 'Andi', 'Kusuma', 'andi@example.com', '082212345678', 'Makassar', 'Indonesia', '2025-07-22 13:10:00'),
(8, 'Lisa', 'Chen', 'lisa@example.com', '083312345678', 'Bali', 'Indonesia', '2025-08-30 17:30:00'),
(9, 'Dimas', 'Rahardian', 'dimas@example.com', '084412345678', 'Malang', 'Indonesia', '2025-09-14 12:00:00'),
(10, 'Nina', 'Sari', 'nina@example.com', '085512345678', 'Palembang', 'Indonesia', '2025-10-25 15:45:00');

-- ── Products ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_DEMO.products (
  product_id    BIGINT        NOT NULL,
  product_name  VARCHAR(200)  NOT NULL,
  category      VARCHAR(80)   NOT NULL,
  brand         VARCHAR(80),
  price         DECIMAL(12,2) NOT NULL,
  stock_qty     INT           NOT NULL,
  created_at    DATETIME      NOT NULL
) PRIMARY KEY(product_id)
DISTRIBUTED BY HASH(product_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_DEMO.products VALUES
(1, 'Laptop ASUS ROG Strix G16', 'Electronics', 'ASUS', 18999000.00, 25, '2025-01-01 00:00:00'),
(2, 'iPhone 16 Pro Max 256GB', 'Electronics', 'Apple', 24999000.00, 50, '2025-01-01 00:00:00'),
(3, 'Samsung Galaxy S25 Ultra', 'Electronics', 'Samsung', 19999000.00, 40, '2025-01-01 00:00:00'),
(4, 'Sony WH-1000XM5 Headphones', 'Audio', 'Sony', 4299000.00, 100, '2025-01-15 00:00:00'),
(5, 'Mechanical Keyboard Keychron Q1', 'Accessories', 'Keychron', 2899000.00, 80, '2025-02-01 00:00:00'),
(6, 'LG UltraGear 27 4K Monitor', 'Electronics', 'LG', 6499000.00, 30, '2025-02-15 00:00:00'),
(7, 'Logitech MX Master 3S Mouse', 'Accessories', 'Logitech', 1499000.00, 120, '2025-03-01 00:00:00'),
(8, 'iPad Air M3 256GB', 'Electronics', 'Apple', 12999000.00, 35, '2025-03-15 00:00:00'),
(9, 'Samsung 990 PRO SSD 2TB', 'Storage', 'Samsung', 3299000.00, 60, '2025-04-01 00:00:00'),
(10, 'AirPods Pro 3', 'Audio', 'Apple', 3999000.00, 90, '2025-04-15 00:00:00'),
(11, 'Dell XPS 15 Laptop', 'Electronics', 'Dell', 22499000.00, 20, '2025-05-01 00:00:00'),
(12, 'Anker 65W USB-C Charger', 'Accessories', 'Anker', 599000.00, 200, '2025-05-15 00:00:00');

-- ── Orders ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_DEMO.orders (
  order_id      BIGINT        NOT NULL,
  customer_id   BIGINT        NOT NULL,
  order_date    DATETIME      NOT NULL,
  status        VARCHAR(20)   NOT NULL DEFAULT 'pending',
  total_amount  DECIMAL(14,2) NOT NULL,
  payment_method VARCHAR(30),
  shipping_city VARCHAR(80)
) PRIMARY KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_DEMO.orders VALUES
(1001, 1, '2025-06-01 10:30:00', 'completed', 23298000.00, 'DANA', 'Jakarta'),
(1002, 2, '2025-06-02 14:00:00', 'completed', 4299000.00, 'GoPay', 'Surabaya'),
(1003, 3, '2025-06-03 09:15:00', 'completed', 18999000.00, 'Bank Transfer', 'Bandung'),
(1004, 1, '2025-06-05 16:45:00', 'completed', 2899000.00, 'QRIS', 'Jakarta'),
(1005, 4, '2025-06-07 11:20:00', 'shipped', 24999000.00, 'Credit Card', 'Semarang'),
(1006, 5, '2025-06-10 08:00:00', 'completed', 6499000.00, 'DANA', 'Yogyakarta'),
(1007, 6, '2025-06-12 13:10:00', 'processing', 19999000.00, 'Bank Transfer', 'Medan'),
(1008, 2, '2025-06-15 17:30:00', 'completed', 1499000.00, 'GoPay', 'Surabaya'),
(1009, 7, '2025-06-18 12:00:00', 'shipped', 12999000.00, 'Credit Card', 'Makassar'),
(1010, 8, '2025-06-20 15:45:00', 'completed', 3999000.00, 'QRIS', 'Bali'),
(1011, 3, '2025-06-22 10:00:00', 'pending', 3299000.00, 'DANA', 'Bandung'),
(1012, 9, '2025-06-25 14:30:00', 'completed', 22499000.00, 'Bank Transfer', 'Malang'),
(1013, 10, '2025-06-28 09:00:00', 'shipped', 599000.00, 'GoPay', 'Palembang'),
(1014, 1, '2025-07-01 11:00:00', 'completed', 4299000.00, 'QRIS', 'Jakarta'),
(1015, 5, '2025-07-05 16:00:00', 'completed', 24999000.00, 'Credit Card', 'Yogyakarta');

-- ── Order Items ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_DEMO.order_items (
  item_id       BIGINT        NOT NULL,
  order_id      BIGINT        NOT NULL,
  product_id    BIGINT        NOT NULL,
  quantity      INT           NOT NULL,
  unit_price    DECIMAL(12,2) NOT NULL,
  subtotal      DECIMAL(14,2) NOT NULL
) PRIMARY KEY(item_id)
DISTRIBUTED BY HASH(item_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_DEMO.order_items VALUES
(1, 1001, 1, 1, 18999000.00, 18999000.00),
(2, 1001, 4, 1, 4299000.00, 4299000.00),
(3, 1002, 4, 1, 4299000.00, 4299000.00),
(4, 1003, 1, 1, 18999000.00, 18999000.00),
(5, 1004, 5, 1, 2899000.00, 2899000.00),
(6, 1005, 2, 1, 24999000.00, 24999000.00),
(7, 1006, 6, 1, 6499000.00, 6499000.00),
(8, 1007, 3, 1, 19999000.00, 19999000.00),
(9, 1008, 7, 1, 1499000.00, 1499000.00),
(10, 1009, 8, 1, 12999000.00, 12999000.00),
(11, 1010, 10, 1, 3999000.00, 3999000.00),
(12, 1011, 9, 1, 3299000.00, 3299000.00),
(13, 1012, 11, 1, 22499000.00, 22499000.00),
(14, 1013, 12, 1, 599000.00, 599000.00),
(15, 1014, 4, 1, 4299000.00, 4299000.00),
(16, 1015, 2, 1, 24999000.00, 24999000.00);

SELECT 'NOVA_DEMO e-commerce sample data loaded!' AS status;
