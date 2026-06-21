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
CREATE USER IF NOT EXISTS 'nova_admin' IDENTIFIED BY '!1password';
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

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES (
  id           VARCHAR(64) NOT NULL,
  user_name    VARCHAR(128) NOT NULL,
  parent_path  VARCHAR(1024) NOT NULL,
  name         VARCHAR(256) NOT NULL,
  entry_type   VARCHAR(32) NOT NULL,
  object_key   VARCHAR(1024),
  size_bytes   BIGINT DEFAULT "0",
  etag         VARCHAR(256),
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  deleted_at   DATETIME,
  is_deleted   BOOLEAN DEFAULT "false"
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AI_PROVIDERS (
  id             VARCHAR(64) NOT NULL,
  name           VARCHAR(128) NOT NULL,
  type           VARCHAR(32) NOT NULL,
  endpoint       VARCHAR(512) NOT NULL,
  api_key        VARCHAR(512),
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
-- ML Schema
-- ═══════════════════════════════════════

-- LLM Function Aliases: maps SQL function names (AI_COMPLETE, AI_SENTIMENT, etc.)
-- to provider+model configs. Backend reads this to register SQL UDFs.
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_MODEL_ALIASES (
  id              VARCHAR(64) NOT NULL,
  alias_name      VARCHAR(128) NOT NULL,
  function_type   VARCHAR(32) NOT NULL,
  provider_id     VARCHAR(64) NOT NULL,
  model_id        VARCHAR(64),
  system_prompt   TEXT,
  default_params  TEXT,
  is_default      BOOLEAN DEFAULT "true",
  is_active       BOOLEAN DEFAULT "true",
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by      VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

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
  query_id      VARCHAR(36),
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
  rewritten_sql TEXT,
  file_id       VARCHAR(64),
  database_name VARCHAR(128),
  schema_name   VARCHAR(128)
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

-- ── Sales: Customers ──────────────────────────────────────────
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

-- ── Sales: Orders ─────────────────────────────────────────────
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

-- ── Sales: Order Items ────────────────────────────────────────
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


-- ═══════════════════════════════════════════════════════════════
-- NOVA_CATALOG — Product Catalog & Inventory Database
-- ═══════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS NOVA_CATALOG;

-- ── Products ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_CATALOG.products (
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

INSERT INTO NOVA_CATALOG.products VALUES
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

-- ── Categories ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_CATALOG.categories (
  category_id   BIGINT        NOT NULL,
  category_name VARCHAR(80)   NOT NULL,
  parent_id     BIGINT,
  description   VARCHAR(255)
) PRIMARY KEY(category_id)
DISTRIBUTED BY HASH(category_id) BUCKETS 2
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_CATALOG.categories VALUES
(1, 'Electronics', NULL, 'Gadgets and electronic devices'),
(2, 'Audio', NULL, 'Headphones, speakers, and audio equipment'),
(3, 'Accessories', NULL, 'Peripherals and add-on accessories'),
(4, 'Storage', NULL, 'SSDs, HDDs, and storage devices'),
(5, 'Laptops', 1, 'Notebook computers and laptops'),
(6, 'Smartphones', 1, 'Mobile phones and tablets'),
(7, 'Monitors', 1, 'Display monitors and screens');

-- ── Warehouses ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_CATALOG.warehouses (
  warehouse_id  BIGINT        NOT NULL,
  warehouse_name VARCHAR(100) NOT NULL,
  city          VARCHAR(80)   NOT NULL,
  capacity      INT           NOT NULL,
  manager       VARCHAR(80)
) PRIMARY KEY(warehouse_id)
DISTRIBUTED BY HASH(warehouse_id) BUCKETS 2
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_CATALOG.warehouses VALUES
(1, 'Jakarta Central Hub', 'Jakarta', 50000, 'Hendra Wijaya'),
(2, 'Surabaya East DC', 'Surabaya', 30000, 'Siti Rahayu'),
(3, 'Bandung West DC', 'Bandung', 20000, 'Agus Prabowo'),
(4, 'Medan North DC', 'Medan', 15000, 'Ratna Dewi');


-- ═══════════════════════════════════════════════════════════════
-- NOVA_ANALYTICS — Business Intelligence Sample Database
-- ═══════════════════════════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS NOVA_ANALYTICS;

-- ── Marketing: Campaigns ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_ANALYTICS.campaigns (
  campaign_id   BIGINT        NOT NULL,
  campaign_name VARCHAR(150)  NOT NULL,
  channel       VARCHAR(40)   NOT NULL,
  start_date    DATE          NOT NULL,
  end_date      DATE,
  budget        DECIMAL(14,2) NOT NULL,
  status        VARCHAR(20)   NOT NULL DEFAULT 'draft'
) PRIMARY KEY(campaign_id)
DISTRIBUTED BY HASH(campaign_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_ANALYTICS.campaigns VALUES
(1, 'Ramadan Sale 2025', 'Instagram', '2025-03-01', '2025-04-10', 50000000.00, 'completed'),
(2, 'Harbolnas 12.12', 'Google Ads', '2025-12-01', '2025-12-15', 75000000.00, 'completed'),
(3, 'Back to School', 'TikTok', '2025-06-15', '2025-07-31', 30000000.00, 'completed'),
(4, 'Tech Week Flash Sale', 'Email', '2025-08-01', '2025-08-07', 10000000.00, 'completed'),
(5, 'Year End Clearance', 'Meta Ads', '2025-12-20', '2026-01-05', 60000000.00, 'active'),
(6, 'New Year New Gadgets', 'Google Ads', '2026-01-01', '2026-01-31', 45000000.00, 'active'),
(7, 'Valentine Special', 'Instagram', '2026-02-01', '2026-02-14', 20000000.00, 'draft'),
(8, 'Brand Awareness Q1', 'YouTube', '2026-01-15', '2026-03-31', 80000000.00, 'active');

-- ── Marketing: Channel Performance ─────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_ANALYTICS.channel_performance (
  record_id     BIGINT        NOT NULL,
  channel       VARCHAR(40)   NOT NULL,
  report_date   DATE          NOT NULL,
  impressions   BIGINT        NOT NULL,
  clicks        BIGINT        NOT NULL,
  conversions   INT           NOT NULL,
  spend         DECIMAL(14,2) NOT NULL
) PRIMARY KEY(record_id)
DISTRIBUTED BY HASH(record_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_ANALYTICS.channel_performance VALUES
(1, 'Instagram', '2025-06-01', 1250000, 43750, 875, 5200000.00),
(2, 'Google Ads', '2025-06-01', 980000, 58800, 1176, 8500000.00),
(3, 'TikTok', '2025-06-01', 2100000, 63000, 945, 3800000.00),
(4, 'Email', '2025-06-01', 150000, 12000, 720, 800000.00),
(5, 'Meta Ads', '2025-06-01', 1800000, 54000, 1080, 6500000.00),
(6, 'YouTube', '2025-06-01', 750000, 22500, 450, 4200000.00),
(7, 'Instagram', '2025-07-01', 1400000, 49000, 980, 5800000.00),
(8, 'Google Ads', '2025-07-01', 1100000, 66000, 1320, 9200000.00),
(9, 'TikTok', '2025-07-01', 2500000, 75000, 1125, 4100000.00),
(10, 'Email', '2025-07-01', 180000, 14400, 864, 900000.00),
(11, 'Meta Ads', '2025-07-01', 2000000, 60000, 1200, 7000000.00),
(12, 'YouTube', '2025-07-01', 900000, 27000, 540, 4800000.00);

-- ── Finance: Invoices ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_ANALYTICS.invoices (
  invoice_id    BIGINT        NOT NULL,
  customer_name VARCHAR(100)  NOT NULL,
  issue_date    DATE          NOT NULL,
  due_date      DATE          NOT NULL,
  amount        DECIMAL(14,2) NOT NULL,
  tax_amount    DECIMAL(12,2) NOT NULL,
  status        VARCHAR(20)   NOT NULL DEFAULT 'unpaid'
) PRIMARY KEY(invoice_id)
DISTRIBUTED BY HASH(invoice_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_ANALYTICS.invoices VALUES
(1, 'PT Teknologi Nusantara', '2025-06-01', '2025-06-30', 125000000.00, 13750000.00, 'paid'),
(2, 'CV Maju Bersama', '2025-06-05', '2025-07-05', 45000000.00, 4950000.00, 'paid'),
(3, 'PT Digital Kreatif', '2025-06-10', '2025-07-10', 78500000.00, 8635000.00, 'paid'),
(4, 'UD Sumber Rejeki', '2025-06-15', '2025-07-15', 32000000.00, 3520000.00, 'overdue'),
(5, 'PT Global Solusi', '2025-07-01', '2025-07-31', 95000000.00, 10450000.00, 'paid'),
(6, 'CV Karya Mandiri', '2025-07-10', '2025-08-10', 28000000.00, 3080000.00, 'paid'),
(7, 'PT Inovasi Data', '2025-07-20', '2025-08-20', 150000000.00, 16500000.00, 'unpaid'),
(8, 'PT Awan Teknologi', '2025-08-01', '2025-08-31', 67000000.00, 7370000.00, 'paid'),
(9, 'CV Bintang Timur', '2025-08-15', '2025-09-15', 41000000.00, 4510000.00, 'unpaid'),
(10, 'PT Sentosa Abadi', '2025-09-01', '2025-09-30', 88000000.00, 9680000.00, 'paid');

-- ── Finance: Monthly Revenue ───────────────────────────────────
CREATE TABLE IF NOT EXISTS NOVA_ANALYTICS.monthly_revenue (
  record_id     BIGINT        NOT NULL,
  report_month  DATE          NOT NULL,
  gross_revenue DECIMAL(16,2) NOT NULL,
  refunds       DECIMAL(14,2) NOT NULL,
  net_revenue   DECIMAL(16,2) NOT NULL,
  order_count   INT           NOT NULL,
  avg_order_value DECIMAL(12,2) NOT NULL
) PRIMARY KEY(record_id)
DISTRIBUTED BY HASH(record_id) BUCKETS 2
PROPERTIES("replication_num"="1");

INSERT INTO NOVA_ANALYTICS.monthly_revenue VALUES
(1, '2025-06-01', 175000000.00, 3500000.00, 171500000.00, 2450, 71428571.00),
(2, '2025-07-01', 192000000.00, 4800000.00, 187200000.00, 2680, 71641791.00),
(3, '2025-08-01', 210000000.00, 5200000.00, 204800000.00, 2900, 72413793.00),
(4, '2025-09-01', 185000000.00, 3700000.00, 181300000.00, 2550, 72549020.00),
(5, '2025-10-01', 225000000.00, 6000000.00, 219000000.00, 3100, 72580645.00),
(6, '2025-11-01', 310000000.00, 9300000.00, 300700000.00, 4200, 73809524.00),
(7, '2025-12-01', 480000000.00, 14400000.00, 465600000.00, 6500, 73846154.00);

SELECT 'NOVA_DEMO + NOVA_CATALOG + NOVA_ANALYTICS sample data loaded!' AS status;

-- ============================================================================
-- Nova Built-in AI/ML UDFs
-- ============================================================================
-- These SQL UDFs are registered as "built-in" functions that behave like
-- native StarRocks functions. They wrap the built-in ai_query() function
-- for LLM inference, and provide placeholder ML_PREDICT for classical ML.
--
-- On backend startup, the llm_functions module will DROP and re-register
-- these UDFs with actual provider config (if aliases are configured).
-- Without aliases, they return a helpful error message.
--
-- DROP protection: These UDFs should NOT be dropped manually.
-- The backend auto-registers them on every startup.
-- ============================================================================

-- AI_COMPLETE: General LLM completion
DROP GLOBAL FUNCTION IF EXISTS AI_COMPLETE(STRING);
CREATE GLOBAL FUNCTION AI_COMPLETE(prompt STRING)
RETURNS CONCAT('ERROR: AI_COMPLETE not configured. Set up an alias in AI Providers > Functions tab. Input was: prompt=', prompt);

-- AI_SENTIMENT: Sentiment analysis
DROP GLOBAL FUNCTION IF EXISTS AI_SENTIMENT(STRING);
CREATE GLOBAL FUNCTION AI_SENTIMENT(txt STRING)
RETURNS CONCAT('ERROR: AI_SENTIMENT not configured. Set up an alias in AI Providers > Functions tab. Input was: txt=', txt);

-- AI_CLASSIFY: Zero-shot classification
DROP GLOBAL FUNCTION IF EXISTS AI_CLASSIFY(STRING, STRING);
CREATE GLOBAL FUNCTION AI_CLASSIFY(txt STRING, categories STRING)
RETURNS CONCAT('ERROR: AI_CLASSIFY not configured. Set up an alias in AI Providers > Functions tab. Input was: txt=', txt, ', categories=', categories);

-- AI_SUMMARIZE: Text summarization
DROP GLOBAL FUNCTION IF EXISTS AI_SUMMARIZE(STRING);
CREATE GLOBAL FUNCTION AI_SUMMARIZE(txt STRING)
RETURNS CONCAT('ERROR: AI_SUMMARIZE not configured. Set up an alias in AI Providers > Functions tab. Input was: txt=', txt);

-- AI_EXTRACT: Entity extraction
DROP GLOBAL FUNCTION IF EXISTS AI_EXTRACT(STRING, STRING);
CREATE GLOBAL FUNCTION AI_EXTRACT(txt STRING, json_schema STRING)
RETURNS CONCAT('ERROR: AI_EXTRACT not configured. Set up an alias in AI Providers > Functions tab. Input was: txt=', txt, ', json_schema=', json_schema);

-- AI_TRANSLATE: Text translation
DROP GLOBAL FUNCTION IF EXISTS AI_TRANSLATE(STRING, STRING);
CREATE GLOBAL FUNCTION AI_TRANSLATE(txt STRING, target_lang STRING)
RETURNS CONCAT('ERROR: AI_TRANSLATE not configured. Set up an alias in AI Providers > Functions tab. Input was: txt=', txt, ', target_lang=', target_lang);

-- AI_FILTER: Semantic boolean filter
DROP GLOBAL FUNCTION IF EXISTS AI_FILTER(STRING, STRING);
CREATE GLOBAL FUNCTION AI_FILTER(txt STRING, criteria STRING)
RETURNS CONCAT('ERROR: AI_FILTER not configured. Set up an alias in AI Providers > Functions tab. Input was: txt=', txt, ', criteria=', criteria);

-- ML_PREDICT: Classical ML prediction (calls backend API)
DROP GLOBAL FUNCTION IF EXISTS ML_PREDICT(STRING, STRING);
CREATE GLOBAL FUNCTION ML_PREDICT(model_alias STRING, features_json STRING)
RETURNS CONCAT('Use POST /api/v1/ml/predict with {"model_alias":"', model_alias, '","features":', features_json, '} to get prediction');

-- Grant USAGE to all roles with all signature variants
-- (STRING, VARCHAR, VARCHAR(65533)) so UDFs behave like native built-ins
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_COMPLETE(VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SENTIMENT(VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_SUMMARIZE(VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(STRING, STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(STRING, STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(STRING, STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(STRING, STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(STRING, STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR, VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR, VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR, VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR, VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR, VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR(65533), VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR(65533), VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR(65533), VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR(65533), VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_CLASSIFY(VARCHAR(65533), VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(STRING, STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(STRING, STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(STRING, STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(STRING, STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(STRING, STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR, VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR, VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR, VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR, VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR, VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR(65533), VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR(65533), VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR(65533), VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR(65533), VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_EXTRACT(VARCHAR(65533), VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(STRING, STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(STRING, STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(STRING, STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(STRING, STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(STRING, STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR, VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR, VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR, VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR, VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR, VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR(65533), VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR(65533), VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR(65533), VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR(65533), VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_TRANSLATE(VARCHAR(65533), VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(STRING, STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(STRING, STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(STRING, STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(STRING, STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(STRING, STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR, VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR, VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR, VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR, VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR, VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR(65533), VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR(65533), VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR(65533), VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR(65533), VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION AI_FILTER(VARCHAR(65533), VARCHAR(65533)) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(STRING, STRING) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(STRING, STRING) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(STRING, STRING) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(STRING, STRING) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(STRING, STRING) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR, VARCHAR) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR, VARCHAR) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR, VARCHAR) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR, VARCHAR) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR, VARCHAR) TO ROLE ACCOUNTADMIN;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR(65533), VARCHAR(65533)) TO ROLE root;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR(65533), VARCHAR(65533)) TO ROLE db_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR(65533), VARCHAR(65533)) TO ROLE cluster_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR(65533), VARCHAR(65533)) TO ROLE user_admin;
GRANT USAGE ON GLOBAL FUNCTION ML_PREDICT(VARCHAR(65533), VARCHAR(65533)) TO ROLE ACCOUNTADMIN;

SELECT 'Nova built-in AI/ML UDFs registered and granted!' AS status;
