# StarRocks Objects — Complete Reference Guide

> **Version**: StarRocks 4.1.1 | **Last Updated**: 2025-06-20
> **Mode**: Shared-nothing (single FE + single BE)

---

## Table of Contents

1. [Object Hierarchy](#object-hierarchy)
2. [Catalog](#catalog)
3. [Database](#database)
4. [Table](#table)
5. [View](#view)
6. [Materialized View](#materialized-view)
7. [Function](#function)
8. [Task](#task)
9. [Pipe](#pipe)
10. [Resource](#resource)
11. [Resource Group](#resource-group)
12. [Warehouse](#warehouse)
13. [Storage Volume](#storage-volume)
14. [Load Jobs](#load-jobs)
15. [Routine Load](#routine-load)
16. [Backup & Restore](#backup--restore)
17. [Cluster Nodes](#cluster-nodes)
18. [System Databases](#system-databases)
19. [SHOW Commands Quick Reference](#show-commands-quick-reference)
20. [information_schema Tables (58)](#information_schema-tables)

---

## Object Hierarchy

```
StarRocks Cluster
│
├── 📦 CATALOG                          ← Top-level namespace
│   ├── default_catalog (Internal)      ← All your data lives here
│   ├── hive_catalog (External)         ← Hive Metastore connector
│   ├── iceberg_catalog (External)      ← Apache Iceberg connector
│   ├── hudi_catalog (External)         ← Apache Hudi connector
│   ├── deltalake_catalog (External)    ← Delta Lake connector
│   ├── paimon_catalog (External)       ← Apache Paimon connector
│   └── kudu_catalog (External)         ← Apache Kudu connector
│
│   └── 🗄️ DATABASE (= Schema)          ← Logical grouping
│       ├── 📋 TABLE                    ← Data storage
│       ├── 👁️ VIEW                     ← Virtual table (saved query)
│       ├── 📊 MATERIALIZED VIEW        ← Pre-computed table (auto-refresh)
│       ├── 🔄 PIPE                     ← Continuous file ingestion
│       └── ⚙️ FUNCTION (UDF)           ← User-defined function
│
├── 🔧 SYSTEM-LEVEL OBJECTS (not inside catalogs)
│   ├── 👤 USER                         ← Database user
│   ├── 🎭 ROLE                         ← Privilege container
│   ├── 📦 RESOURCE                     ← External compute (Spark, Broker)
│   ├── 🏗️ RESOURCE GROUP               ← Workload isolation
│   ├── 💾 STORAGE VOLUME               ← Remote storage (shared-data)
│   ├── 🏭 WAREHOUSE                    ← Compute warehouse (shared-data)
│   ├── 🌐 GLOBAL FUNCTION              ← Instance-level UDF
│   └── 📚 REPOSITORY                   ← Backup destination
│
└── 🖥️ CLUSTER INFRASTRUCTURE
    ├── FE (Frontend)                   ← Query planner + metadata
    ├── BE (Backend)                    ← Storage + compute engine
    └── CN (Compute Node)              ← Stateless compute (shared-data)
```

### Key Concepts

| Concept | Description |
|---------|-------------|
| **No Schema Layer** | StarRocks has NO schema inside database. `Database = Schema`. Flat 2-level: `Catalog → Database` |
| **Three-Part Notation** | `catalog.database.table` — fully qualified reference |
| **Session Context** | `SET CATALOG x` + `USE db` sets the working context |
| **information_schema** | Uses `def` as catalog name (MySQL compat), NOT `default_catalog` |

---

## Catalog

> **Position**: Topmost namespace. Every database belongs to exactly one catalog.

### What Is It?

A catalog is a **data source boundary**. The internal catalog holds all StarRocks-managed data. External catalogs connect to outside data sources (Hive, Iceberg, etc.) and make their tables queryable via standard SQL.

### Types

| Type | Property `'type'` | Description |
|------|-------------------|-------------|
| **Internal** | (built-in) | `default_catalog` — all your databases/tables |
| **Hive** | `hive` | Hive Metastore connector |
| **Iceberg** | `iceberg` | Apache Iceberg connector |
| **Hudi** | `hudi` | Apache Hudi connector |
| **Delta Lake** | `deltalake` | Delta Lake connector |
| **Paimon** | `paimon` | Apache Paimon connector |
| **Kudu** | `kudu` | Apache Kudu connector |
| **JDBC** | `jdbc` | JDBC connector (PostgreSQL, MySQL, Oracle, SQL Server) |

### SQL Commands

| Command | Syntax | Notes |
|---------|--------|-------|
| List | `SHOW CATALOGS;` | Shows Catalog, Type, Comment |
| Create | `CREATE EXTERNAL CATALOG name PROPERTIES ('type'='hive', 'hive.metastore.uris'='thrift://...');` | |
| Create with comment | `CREATE EXTERNAL CATALOG name COMMENT 'desc' PROPERTIES (...);` | COMMENT keyword (not property) |
| Alter | `ALTER CATALOG name SET ('key'='value');` | Only SET supported, no RENAME |
| Drop | `DROP CATALOG [IF EXISTS] name;` | Cannot drop `default_catalog` |
| Show DDL | `SHOW CREATE CATALOG name;` | Shows PROPERTIES for external catalogs |
| Switch | `SET CATALOG name;` | Sets session catalog |
| Current | `SELECT @@session.catalog;` | Returns current catalog name |
| List DBs | `SHOW DATABASES FROM catalog_name;` | Lists databases in a catalog |
| Query | `SELECT * FROM catalog.db.table;` | Three-part notation |

### Properties (by type)

**Hive:**
```
'type' = 'hive'
'hive.metastore.type' = 'hive'
'hive.metastore.uris' = 'thrift://host:9083'
```

**Iceberg:**
```
'type' = 'iceberg'
'iceberg.catalog.type' = 'hive'
'iceberg.catalog.hive.metastore.uris' = 'thrift://host:9083'
```

**JDBC:**
```
'type' = 'jdbc'
'jdbc_uri' = 'jdbc:postgresql://host:5432/db'
'user' = 'username'
'password' = 'password'
'driver_url' = 'postgresql-42.x.jar'
'driver_class' = 'org.postgresql.Driver'
```

### ⚠️ Pitfalls

- `USE CATALOG x` does NOT work — parses as `USE database 'CATALOG'`
- `CURRENT_CATALOG()` function does NOT exist
- `information_schema.catalogs` table does NOT exist
- `information_schema` views use `def` as catalog name, not `default_catalog`

---

## Database

> **Position**: Inside a catalog. Contains tables, views, MVs, pipes, functions.

### What Is It?

A database is a **logical namespace** for organizing data objects. In StarRocks, **Database = Schema** — there is no separate schema layer.

### SQL Commands

| Command | Syntax | Notes |
|---------|--------|-------|
| List | `SHOW DATABASES;` | Lists in current catalog |
| List (other catalog) | `SHOW DATABASES FROM catalog;` | |
| Create | `CREATE DATABASE [IF NOT EXISTS] name;` | |
| Drop | `DROP DATABASE [IF EXISTS] name;` | |
| Alter | `ALTER DATABASE name RENAME new_name;` | |
| Use | `USE db_name;` | Sets current database |
| Show DDL | `SHOW CREATE DATABASE name;` | |
| Current | `SELECT DATABASE();` | Returns current DB or NULL |

### Properties

```sql
SHOW CREATE DATABASE NOVA_SYSTEM;
-- Output: CREATE DATABASE `NOVA_SYSTEM`
```

Minimal — databases in StarRocks have no charset/collation/properties beyond the name.

### System Databases (built-in)

| Database | Purpose | Tables |
|----------|---------|--------|
| `information_schema` | SQL-standard metadata views | 58 views |
| `sys` | StarRocks-specific diagnostics | 6 views |
| `_statistics_` | Internal query/table statistics | 13 tables |

---

## Table

> **Position**: Inside a database. The primary data storage object.

### What Is It?

Tables are the core storage unit. StarRocks is a columnar OLAP database — all tables are stored in columnar format with automatic compression.

### Table Model Types

StarRocks has **4 table models**, each optimized for different access patterns:

| Model | DDL Marker | Use Case | UPDATE/DELETE | Key Feature |
|-------|-----------|----------|:---:|-------------|
| **Primary Key** | `PRIMARY KEY(...)` | CRUD operations, upserts | ✅ | Row-level updates/deletes via persistent index |
| **Duplicate Key** | `DUPLICATE KEY(...)` | Append-only, event data, logs | ❌ | All rows kept (duplicates allowed) |
| **Unique Key** | `UNIQUE KEY(...)` | Legacy dedup | ✅ (merge-on-read) | Older model, superseded by Primary Key |
| **Aggregate Key** | `AGGREGATE KEY(...)` | Pre-aggregated metrics | ❌ | Auto-aggregation on ingest |

### Partitioning

| Type | Syntax | Example |
|------|--------|---------|
| **RANGE** | `PARTITION BY RANGE(col) (...)` | Monthly partitions on date column |
| **LIST** | `PARTITION BY LIST(col) (...)` | Categorical partitions |
| **Expression** | `PARTITION BY date_trunc('DAY', col)` | Auto-partition by time function |
| **Unpartitioned** | (default) | Single partition |

### Distribution

| Type | Syntax | Notes |
|------|--------|-------|
| **HASH** | `DISTRIBUTED BY HASH(col) BUCKETS N` | Even distribution by hash |
| **RANDOM** | `DISTRIBUTED BY RANDOM` | Random distribution |
| **Auto buckets** | `DISTRIBUTED BY HASH(col)` (no BUCKETS) | Auto-sizing |

### SQL Commands

| Command | Syntax |
|---------|--------|
| List | `SHOW TABLES;` / `SHOW TABLES FROM db;` |
| Create | `CREATE TABLE name (...) ENGINE=OLAP PRIMARY KEY(...) DISTRIBUTED BY HASH(...) BUCKETS N PROPERTIES (...);` |
| Drop | `DROP TABLE [IF EXISTS] name;` |
| Alter | `ALTER TABLE name ADD COLUMN col TYPE;` / `ALTER TABLE name DROP COLUMN col;` |
| Truncate | `TRUNCATE TABLE name;` |
| Show DDL | `SHOW CREATE TABLE name;` |
| Describe | `DESC table_name;` / `SHOW COLUMNS FROM table_name;` |
| Partitions | `SHOW PARTITIONS FROM table_name;` |
| Metadata | `SELECT * FROM information_schema.tables WHERE TABLE_SCHEMA='db';` |
| Config | `SELECT * FROM information_schema.tables_config WHERE TABLE_SCHEMA='db';` |

### DDL Example (Primary Key)

```sql
CREATE TABLE `users` (
  `id` bigint NOT NULL,
  `name` varchar(128) NOT NULL,
  `email` varchar(256) NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP
) ENGINE=OLAP
PRIMARY KEY(`id`)
DISTRIBUTED BY HASH(`id`) BUCKETS 1
PROPERTIES (
  "compression" = "LZ4",
  "enable_persistent_index" = "true",
  "fast_schema_evolution" = "true",
  "replication_num" = "1"
);
```

### DDL Example (Duplicate Key with Partitions)

```sql
CREATE TABLE `audit_log` (
  `log_id` bigint NOT NULL AUTO_INCREMENT,
  `event_type` varchar(64) NOT NULL,
  `event_time` datetime NOT NULL,
  `user_name` varchar(128) NULL,
  `sql_text` varchar(65533) NULL
) ENGINE=OLAP
DUPLICATE KEY(`log_id`, `event_type`, `event_time`)
PARTITION BY RANGE(`event_time`) (
  PARTITION p202601 VALUES [("2026-01-01"), ("2026-02-01")),
  PARTITION p202602 VALUES [("2026-02-01"), ("2026-03-01"))
)
DISTRIBUTED BY HASH(`log_id`) BUCKETS 8;
```

### Table Properties

| Property | Default | Description |
|----------|---------|-------------|
| `replication_num` | 3 (or 1 single-node) | Number of replicas |
| `compression` | LZ4 | Compression algorithm (LZ4, ZSTD, ZLIB) |
| `enable_persistent_index` | false | Primary Key persistent index (improves update perf) |
| `fast_schema_evolution` | true | Enable fast schema changes |
| `storage_medium` | HDD | Storage tier (HDD, SSD) |
| `bucket_size` | 1GB | Auto-bucket sizing target |
| `dynamic_partition.enable` | false | Auto-create/drop partitions |
| `partition_live_number` | N | Keep N most recent partitions (TTL) |

---

## View

> **Position**: Inside a database. A virtual table defined by a saved query.

### What Is It?

A view is a **named, saved query** that behaves like a read-only table. Views do NOT store data — they execute the underlying query each time they're accessed.

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE VIEW [IF NOT EXISTS] db.name AS SELECT ...;` |
| Create or Replace | `CREATE OR REPLACE VIEW db.name AS SELECT ...;` |
| Alter | `ALTER VIEW db.name AS SELECT ...;` |
| Drop | `DROP VIEW [IF EXISTS] db.name;` |
| Show DDL | `SHOW CREATE VIEW db.name;` |
| List | `SELECT * FROM information_schema.views WHERE TABLE_SCHEMA='db';` |

### Properties

| Column (information_schema.views) | Description |
|-----------------------------------|-------------|
| TABLE_CATALOG | Catalog name (`def`) |
| TABLE_SCHEMA | Database name |
| TABLE_NAME | View name |
| VIEW_DEFINITION | Full SELECT statement |
| CHECK_OPTION | ALWAYS `NONE` |
| IS_UPDATABLE | ALWAYS `NO` |
| DEFINER | Creator user |

### ⚠️ Limitations

- Views are **read-only** — cannot INSERT/UPDATE/DELETE through a view
- No `WITH CHECK OPTION` support
- No materialized views through the VIEW interface (use MATERIALIZED VIEW instead)

---

## Materialized View

> **Position**: Inside a database. A pre-computed table that auto-refreshes.

### What Is It?

A materialized view (MV) stores the **result of a query physically on disk** and can be refreshed automatically on a schedule or when base data changes. MVs are the primary mechanism for **query acceleration** in StarRocks.

### Types

| Type | Refresh | Use Case |
|------|---------|----------|
| **Async MV** | Scheduled (cron) or manual | Complex aggregations, ETL summaries |
| **Sync MV** | Automatic (on base table change) | Simple rollups (single-table aggregations) |

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE MATERIALIZED VIEW name REFRESH ASYNC EVERY (INTERVAL 1 HOUR) AS SELECT ...;` |
| Create (sync) | `ALTER TABLE base ADD ROLLUP name (col1, SUM(col2));` |
| Refresh | `REFRESH MATERIALIZED VIEW name;` |
| Alter | `ALTER MATERIALIZED VIEW name REFRESH ASYNC EVERY (INTERVAL 30 MINUTE);` |
| Drop | `DROP MATERIALIZED VIEW [IF EXISTS] name;` |
| List | `SHOW MATERIALIZED VIEWS [FROM db];` |
| Metadata | `SELECT * FROM information_schema.materialized_views WHERE TABLE_SCHEMA='db';` |

### Key Properties (information_schema.materialized_views)

| Column | Description |
|--------|-------------|
| MATERIALIZED_VIEW_ID | Internal ID |
| REFRESH_TYPE | ASYNC / MANUAL / SYNC |
| IS_ACTIVE | Whether the MV is active |
| LAST_REFRESH_STATE | SUCCESS / FAILED / RUNNING |
| LAST_REFRESH_DURATION | Time taken for last refresh |
| LAST_REFRESH_ERROR_MESSAGE | Error details if failed |
| TABLE_ROWS | Current row count |
| MATERIALIZED_VIEW_DEFINITION | Full SQL definition |
| QUERY_REWRITE_STATUS | Whether query optimizer can rewrite queries to use this MV |
| TASK_NAME | Associated task name (for async refresh) |

---

## Function

> **Position**: Inside a database (UDF) or instance-level (Global Function).

### What Is It?

Functions are **reusable computation units** that can be called in SQL queries. StarRocks has 798 built-in functions plus support for user-defined functions (UDFs).

### Function Types

| Type | Scope | Create Syntax | Privilege |
|------|-------|--------------|-----------|
| **Built-in** | Global | (pre-installed) | USAGE (implicit) |
| **UDF (Java)** | Database | `CREATE FUNCTION db.name(...) RETURNS type PROPERTIES (...)` | USAGE ON FUNCTION |
| **UDF (Python)** | Database | `CREATE FUNCTION db.name(...) RETURNS type LANGUAGE PYTHON PROPERTIES (...)` | USAGE ON FUNCTION |
| **Global UDF** | Instance | `CREATE GLOBAL FUNCTION name(...) RETURNS type PROPERTIES (...)` | USAGE ON GLOBAL FUNCTION |

### Built-in Function Categories (798 total)

| Category | Examples | Count |
|----------|----------|-------|
| **Aggregate** | SUM, AVG, COUNT, MIN, MAX, GROUP_CONCAT, BITMAP_AGG | ~50 |
| **Window** | ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, NTILE | ~15 |
| **String** | CONCAT, SUBSTR, REPLACE, REGEXP, SPLIT, LOWER, UPPER | ~60 |
| **Date/Time** | NOW, DATE_ADD, DATE_DIFF, DATE_TRUNC, STR_TO_DATE | ~40 |
| **Math** | ABS, CEIL, FLOOR, ROUND, LOG, POWER, SQRT | ~30 |
| **Array** | ARRAY_AGG, ARRAY_LENGTH, ARRAY_CONTAINS, UNNEST | ~40 |
| **JSON** | JSON_QUERY, JSON_EXTRACT, PARSE_JSON, JSON_OBJECT | ~15 |
| **Approximate** | APPROX_COUNT_DISTINCT, APPROX_TOP_K, NDV | ~20 |
| **Hash** | MD5, SHA1, SHA256, XX_HASH | ~10 |
| **Encryption** | AES_ENCRYPT, AES_DECRYPT | ~5 |
| **AI** | AI_QUERY, AI_COMPLETE, AI_SENTIMENT | ~5 |
| **Bitmap** | BITMAP_AND, BITMAP_OR, BITMAP_XOR, BITMAP_COUNT | ~15 |
| **Type Cast** | CAST, TRY_CAST | 2 |
| **Conditional** | IF, CASE, COALESCE, NULLIF, IFNULL | ~10 |
| **Iceberg Transform** | __iceberg_transform_bucket, __iceberg_transform_day | ~12 |

### SQL Commands

| Command | Syntax |
|---------|--------|
| List built-in | `SHOW BUILTIN FUNCTIONS;` |
| List UDFs | `SHOW FUNCTIONS [FROM db];` |
| List global | `SHOW GLOBAL FUNCTIONS;` |
| Create UDF | `CREATE FUNCTION db.name(arg_types) RETURNS ret_type PROPERTIES ('type'='StarrocksJar', 'symbol'='...', 'file'='...');` |
| Drop UDF | `DROP FUNCTION db.name(arg_types);` |
| Drop global | `DROP GLOBAL FUNCTION name(arg_types);` |

---

## Task

> **Position**: Instance-level. Auto-managed background job.

### What Is It?

A task is a **scheduled background job** in StarRocks. Tasks are primarily created automatically by the system for materialized view refreshes. In StarRocks 4.1.1, there is no standalone `CREATE TASK` DDL — tasks are managed internally.

### Data Sources

| Source | Schema |
|--------|--------|
| `information_schema.tasks` | TASK_NAME, CREATE_TIME, SCHEDULE, CATALOG, DATABASE, DEFINITION, EXPIRE_TIME, PROPERTIES, CREATOR |
| `information_schema.task_runs` | QUERY_ID, TASK_NAME, CREATE_TIME, FINISH_TIME, STATE, ERROR_CODE, ERROR_MESSAGE, PROGRESS, WAREHOUSE, DATABASE, DEFINITION, JOB_ID, PROCESS_TIME |

### Internal System Tasks (from `SHOW PROC '/tasks'`)

These are internal tablet management operations, not user-facing:

| Task Type | Description |
|-----------|-------------|
| PUSH | Data push to BE |
| CLONE | Tablet replication |
| SCHEMA_CHANGE | Schema evolution |
| COMPACTION | Data compaction |
| ROLLUP | Rollup creation |
| MAKE_SNAPSHOT / RELEASE_SNAPSHOT | Backup snapshots |
| PUBLISH_VERSION | Transaction commit |
| STREAM_LOAD | Stream load processing |
| ALTER | General ALTER operations |
| UPDATE_SCHEMA | Schema update propagation |

### ⚠️ Commands That DON'T Exist

| Command | Status |
|---------|--------|
| `SHOW TASKS` | ❌ Not a valid statement |
| `CREATE TASK` | ❌ Not a valid statement |
| `DROP TASK` | ❌ Not a valid statement |

Tasks are viewed through `information_schema.tasks` only.

---

## Pipe

> **Position**: Inside a database. Continuous file-based ingestion.

### What Is It?

A pipe is a **continuous data ingestion mechanism** that polls a file location (S3, HDFS, local) and automatically loads new files into a target table. Think of it as a "file watcher" that auto-imports data.

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE PIPE pipe_name PROPERTIES (...) AS INSERT INTO target_table SELECT * FROM FILES(...);` |
| List | `SHOW PIPES [FROM db];` |
| Alter | `ALTER PIPE pipe_name SUSPEND;` / `ALTER PIPE pipe_name RESUME;` |
| Drop | `DROP PIPE [IF EXISTS] pipe_name;` |
| Metadata | `SELECT * FROM information_schema.pipes;` |
| File status | `SELECT * FROM information_schema.pipe_files;` |

### Pipe States

| State | Description |
|-------|-------------|
| RUNNING | Actively polling and loading |
| SUSPENDED | Paused |
| FINISHED | All files loaded, no more polling |
| ERROR | Encountered an error |

### information_schema.pipes

| Column | Description |
|--------|-------------|
| PIPE_ID | Internal ID |
| PIPE_NAME | Name |
| DATABASE_NAME | Target database |
| TABLE_NAME | Target table |
| STATE | Current state |
| PROPERTIES | Config JSON (poll interval, etc.) |
| LOAD_STATUS | Current load progress |
| LAST_ERROR | Most recent error |
| CREATED_TIME | Creation timestamp |

### information_schema.pipe_files

| Column | Description |
|--------|-------------|
| FILE_NAME | Source file path |
| FILE_SIZE | Size in bytes |
| LOAD_STATE | LOADED / LOADING / ERROR |
| STAGED_TIME | When discovered |
| START_LOAD_TIME | Load started |
| FINISH_LOAD_TIME | Load completed |
| ERROR_MSG | Error details |

---

## Resource

> **Position**: Instance-level. External compute connector.

### What Is It?

A resource defines an **external compute endpoint** (Spark cluster, Broker service, JDBC connection) that StarRocks can use for ETL jobs or external catalog access.

### Resource Types

| Type | Use Case | Key Properties |
|------|----------|---------------|
| **spark** | Spark ETL | `spark.master`, `spark.hadoop.*` |
| **broker** | HDFS/S3 file access | `broker.name`, `broker.host` |
| **jdbc** | External DB queries | `jdbc_uri`, `user`, `password` |
| **hive** | Hive Metastore | `hive.metastore.uris` |

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE EXTERNAL RESOURCE name PROPERTIES ('type'='spark', ...);` |
| List | `SHOW RESOURCES;` |
| Drop | `DROP RESOURCE name;` |

### ⚠️ Notes

- `information_schema.resources` does NOT exist in 4.1.1
- Resources are primarily used by **Spark ETL** and **Broker Load** features
- Modern usage prefers **External Catalogs** over Resources for data access

---

## Resource Group

> **Position**: Instance-level. Workload isolation.

### What Is It?

A resource group binds users/roles to **CPU and memory quotas**, providing workload isolation. Queries from users in a resource group are constrained by the group's limits.

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE RESOURCE GROUP rg_name TO (user='x', role='y') WITH ('cpu_weight'='1', 'mem_limit'='50%');` |
| List | `SHOW RESOURCE GROUPS;` |
| Alter | `ALTER RESOURCE GROUP rg_name WITH ('mem_limit'='70%');` |
| Drop | `DROP RESOURCE GROUP rg_name;` |
| Set session | `SET resource_group = 'rg_name';` |

### Properties

| Property | Required | Description |
|----------|:--------:|-------------|
| `cpu_weight` | No | Relative CPU weight (default 1) |
| `mem_limit` | **Yes** | Memory limit as percentage (e.g., `50%`) |
| `concurrency_limit` | No | Max concurrent queries |
| `big_query_mem_limit` | No | Memory limit for big queries |
| `big_query_scan_rows` | No | Max scan rows for big queries |
| `big_query_cpu_second` | No | Max CPU seconds for big queries |

---

## Warehouse

> **Position**: Instance-level. Compute resource pool.

### What Is It?

A warehouse is a **named compute resource pool** that executes queries. In shared-nothing mode, there's only one implicit `default_warehouse`. Multi-warehouse is a **shared-data (cloud-native) feature**.

### Current State (shared-nothing)

```
SHOW PROC '/warehouses';
Id: 0  Name: default_warehouse  State: AVAILABLE  NodeCount: 0
```

### SQL Commands (shared-data mode only)

| Command | Syntax |
|---------|--------|
| Create | `CREATE WAREHOUSE name WITH ('size'='medium');` |
| List | `SHOW WAREHOUSES;` |
| Alter | `ALTER WAREHOUSE name SUSPEND;` / `ALTER WAREHOUSE name RESUME;` |
| Drop | `DROP WAREHOUSE name;` |
| Set session | `SET warehouse = 'warehouse_name';` |
| Metrics | `SELECT * FROM information_schema.warehouse_metrics;` |
| Queries | `SELECT * FROM information_schema.warehouse_queries;` |

### information_schema.warehouse_metrics

| Column | Description |
|--------|-------------|
| WAREHOUSE_NAME | Name |
| QUEUE_PENDING_LENGTH | Pending queries |
| QUEUE_RUNNING_LENGTH | Running queries |
| MAX_PENDING_TIME_SECOND | Max queue wait |
| REMAIN_SLOTS | Available slots |
| MAX_SLOTS | Total capacity |

---

## Storage Volume

> **Position**: Instance-level. Remote storage location.

### What Is It?

A storage volume defines a **remote storage location** (S3, GCS, Azure, HDFS) for data persistence in shared-data mode and spill operations.

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE STORAGE VOLUME name TYPE=S3 LOCATIONS=('s3://bucket/path') PROPERTIES (...);` |
| List | `SHOW STORAGE VOLUMES;` |
| Show DDL | `SHOW CREATE STORAGE VOLUME name;` |
| Alter | `ALTER STORAGE VOLUME name SET (...);` |
| Drop | `DROP STORAGE VOLUME name;` |

### Supported Storage Types

| Type | Properties |
|------|-----------|
| **S3** | `aws.s3.access_key`, `aws.s3.secret_key`, `aws.s3.region`, `aws.s3.endpoint` |
| **GCS** | `gcp.gcs.service_account_key` |
| **Azure** | `azure.blob.account_name`, `azure.blob.account_key` |
| **HDFS** | `hdfs.fs.defaultFS` |

### ⚠️ Notes

- In shared-nothing mode, data lives on local BE disks — storage volumes are not needed
- Used for: shared-data mode storage, query spill, backup destinations

---

## Load Jobs

> **Position**: Inside a database. Data import operations.

### What Is It?

Load jobs are **data import operations** that bring external data into StarRocks tables. There are several load methods, each tracked in `information_schema.loads`.

### Load Types

| Type | Method | Use Case |
|------|--------|----------|
| **INSERT** | `INSERT INTO ... VALUES/SELECT` | Small batches, ad-hoc inserts |
| **BROKER** | `LOAD LABEL db.name (...)` | Large batch from HDFS/S3 via Broker |
| **SPARK** | `LOAD LABEL db.name (...) WITH RESOURCE 'spark_resource'` | Spark-based ETL |
| **STREAM** | HTTP PUT to BE port | Real-time streaming from apps |
| **ROUTINE** | `CREATE ROUTINE LOAD` | Continuous Kafka ingestion |

### Load States

| State | Description |
|-------|-------------|
| PENDING | Queued, waiting to start |
| LOADING | In progress |
| COMMITTED | Data committed, visible |
| FINISHED | Complete |
| CANCELLED | Failed or cancelled |

### information_schema.loads (25 columns)

| Column | Description |
|--------|-------------|
| ID | Load job ID |
| LABEL | User-defined label |
| DB_NAME | Target database |
| TABLE_NAME | Target table |
| USER | Who submitted |
| STATE | Current state |
| PROGRESS | Completion percentage |
| TYPE | INSERT / BROKER / SPARK |
| SCAN_ROWS | Source rows scanned |
| SCAN_BYTES | Source bytes read |
| SINK_ROWS | Rows actually loaded |
| FILTERED_ROWS | Rows rejected by filter |
| CREATE_TIME | Job submitted |
| LOAD_START_TIME | Loading started |
| LOAD_FINISH_TIME | Loading completed |
| ERROR_MSG | Error details if failed |

---

## Routine Load

> **Position**: Inside a database. Continuous Kafka ingestion.

### What Is It?

A routine load is a **long-running, continuous data ingestion job** from Apache Kafka into a StarRocks table. It continuously consumes messages and loads them in micro-batches.

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create | `CREATE ROUTINE LOAD db.name ON table COLUMNS TERMINATED BY ',' PROPERTIES (...) FROM KAFKA (...);` |
| List | `SHOW ROUTINE LOAD [FROM db];` |
| Pause | `PAUSE ROUTINE LOAD FOR db.name;` |
| Resume | `RESUME ROUTINE LOAD FOR db.name;` |
| Drop | `DROP ROUTINE LOAD [IF EXISTS] db.name;` |
| Metadata | `SELECT * FROM information_schema.routine_load_jobs;` |

---

## Backup & Restore

> **Position**: Instance-level (Repository) / Database-level (Backup/Restore operations).

### What Is It?

Backup and restore provides **snapshot-based data protection**. A Repository defines where backups are stored, and Backup/Restore operations work on tables within a database.

### SQL Commands

| Command | Syntax |
|---------|--------|
| Create Repository | `CREATE REPOSITORY name WITH BROKER broker_name ON LOCATION 'path' PROPERTIES (...);` |
| List Repositories | `SHOW REPOSITORIES;` |
| Drop Repository | `DROP REPOSITORY name;` |
| Backup | `BACKUP SNAPSHOT db.name TO name ON (table1, table2);` |
| Show Backup | `SHOW BACKUP FROM db;` |
| Restore | `RESTORE SNAPSHOT db.name FROM name ON (table1) PROPERTIES (...);` |
| Show Restore | `SHOW RESTORE FROM db;` |
| Cancel | `CANCEL BACKUP FROM db;` / `CANCEL RESTORE FROM db;` |

### Cluster Snapshots (shared-data)

| Source | Schema |
|--------|--------|
| `information_schema.cluster_snapshots` | SNAPSHOT_NAME, SNAPSHOT_TYPE, CREATED_TIME, STORAGE_VOLUME, STORAGE_PATH |
| `information_schema.cluster_snapshot_jobs` | SNAPSHOT_NAME, JOB_ID, STATE, DETAIL_INFO, ERROR_MESSAGE |

---

## Cluster Nodes

### Frontend (FE)

> **Role**: Query planner, metadata manager, transaction coordinator.

```
SHOW FRONTENDS;
```

| Column | Description |
|--------|-------------|
| Id | Node ID |
| Name | Node name |
| IP | IP address |
| QueryPort | MySQL protocol port (9030) |
| HttpPort | Web UI port (8030) |
| EditLogPort | Replication port (9040) |
| Role | LEADER / FOLLOWER / OBSERVER |
| Alive | Health status |
| Version | StarRocks version |
| ReplayedJournalId | Metadata replication progress |

### Backend (BE)

> **Role**: Data storage + compute engine.

```
SHOW BACKENDS;
```

| Column | Description |
|--------|-------------|
| BackendId | Node ID |
| IP | IP address |
| HeartbeatPort | Heartbeat port (9050) |
| BePort | Data port (9060) |
| HttpPort | HTTP port (8040) |
| Alive | Health status |
| TabletNum | Number of tablets |
| DataUsedCapacity | Data size |
| AvailCapacity | Available disk |
| CpuCores | CPU cores |
| MemLimit | Memory limit |
| NumRunningQueries | Active queries |
| Version | StarRocks version |

### Compute Node (CN)

> **Role**: Stateless compute (shared-data mode only).

```
SHOW COMPUTE NODES;
```

Empty in shared-nothing mode. In shared-data, CNs provide elastic compute without storage.

---

## System Databases

### `information_schema` (58 tables/views)

SQL-standard metadata views. Key tables:

| Table | Description |
|-------|-------------|
| `tables` | All tables with ENGINE, ROWS, DATA_LENGTH |
| `tables_config` | Table model type, distribution, partitions |
| `columns` | Column definitions with types, nullability |
| `views` | View definitions |
| `materialized_views` | MV definitions and refresh status |
| `schemata` | Database list (SCHEMA_NAME = database name) |
| `partitions` | Partition details |
| `loads` | Load job history |
| `pipes` / `pipe_files` | Pipe definitions and file tracking |
| `tasks` / `task_runs` | Background task definitions and run history |
| `routine_load_jobs` | Routine load configurations |
| `fe_metrics` | Frontend metrics (798 metrics) |
| `be_metrics` | Backend metrics (785 metrics) |
| `be_tablets` | Tablet-level storage info |
| `warehouse_metrics` / `warehouse_queries` | Warehouse performance |
| `grants_to_roles` / `grants_to_users` | Privilege assignments (via `sys.*`) |

### `sys` (6 views)

| View | Description |
|------|-------------|
| `grants_to_roles` | All privileges granted to roles |
| `grants_to_users` | All direct privileges granted to users |
| `role_edges` | Role-to-user and role-to-role assignments |
| `object_dependencies` | Object dependency graph (MV → base table) |
| `fe_locks` | Frontend lock diagnostics |
| `fe_memory_usage` | Frontend memory by module |

### `_statistics_` (13 tables)

Internal tables for query optimization:

| Table | Description |
|-------|-------------|
| `column_statistics` | Per-column stats (min, max, NDV, nulls) |
| `histogram_statistics` | Histogram distributions |
| `table_statistic_v1` | Table-level statistics |
| `query_history` | Query execution history |
| `loads_history` | Load job history |
| `task_run_history` | Task execution history |
| `pipe_file_list` | Pipe file tracking |
| `spm_baselines` | SQL Plan Management baselines |

---

## SHOW Commands Quick Reference

| Category | Command | Works? | Notes |
|----------|---------|:------:|-------|
| **Catalog** | `SHOW CATALOGS` | ✅ | |
| | `SHOW CREATE CATALOG name` | ✅ | External only |
| | `SHOW DATABASES FROM catalog` | ✅ | |
| **Database** | `SHOW DATABASES` | ✅ | Current catalog |
| | `SHOW CREATE DATABASE name` | ✅ | |
| **Table** | `SHOW TABLES` | ✅ | Current database |
| | `SHOW CREATE TABLE name` | ✅ | Full DDL |
| | `SHOW COLUMNS FROM name` | ✅ | Column info |
| | `SHOW PARTITIONS FROM name` | ✅ | Partition details |
| | `SHOW TABLE STATUS` | ✅ | MySQL compat |
| **View** | `SHOW CREATE VIEW name` | ✅ | |
| **MV** | `SHOW MATERIALIZED VIEWS` | ✅ | Needs DB context |
| | `SHOW CREATE MATERIALIZED VIEW name` | ✅ | |
| **Function** | `SHOW BUILTIN FUNCTIONS` | ✅ | 798 functions |
| | `SHOW FUNCTIONS` | ✅ | UDFs in current DB |
| | `SHOW GLOBAL FUNCTIONS` | ✅ | |
| **Task** | `SHOW TASKS` | ❌ | Use `information_schema.tasks` |
| **Pipe** | `SHOW PIPES` | ✅ | Needs DB context |
| **Resource** | `SHOW RESOURCES` | ✅ | |
| **Res. Group** | `SHOW RESOURCE GROUPS` | ✅ | |
| **Warehouse** | `SHOW WAREHOUSES` | ❌* | Shared-data only |
| **Storage Vol** | `SHOW STORAGE VOLUMES` | ✅ | |
| **Load** | `SHOW LOAD` | ✅ | Needs DB context |
| | `SHOW STREAM LOAD` | ✅ | Needs DB context |
| **Routine Load** | `SHOW ROUTINE LOAD` | ✅ | Needs DB context |
| **Cluster** | `SHOW FRONTENDS` | ✅ | |
| | `SHOW BACKENDS` | ✅ | |
| | `SHOW COMPUTE NODES` | ✅ | |
| | `SHOW PROCESSLIST` | ✅ | Active queries |
| **Auth** | `SHOW USERS` | ✅ | |
| | `SHOW ROLES` | ✅ | |
| | `SHOW GRANTS` | ✅ | Current user |
| | `SHOW GRANTS FOR 'user'@'host'` | ✅ | |
| | `SHOW GRANTS FOR ROLE name` | ✅ | |
| | `SHOW PROPERTY FOR 'user'` | ✅ | |
| **Security** | `SHOW SECURITY INTEGRATIONS` | ✅ | Empty |
| | `SHOW WHITELIST` | ✅ | Empty |
| **Backup** | `SHOW REPOSITORIES` | ✅ | |
| | `SHOW BACKUP` | ✅ | Needs DB context |
| | `SHOW RESTORE` | ✅ | Needs DB context |
| | `SHOW SNAPSHOT` | ✅ | |
| **Config** | `SHOW VARIABLES` | ✅ | Session variables |
| | `SHOW VARIABLES LIKE 'pattern'` | ✅ | |
| | `ADMIN SHOW FRONTEND CONFIG` | ✅ | FE config |
| | `ADMIN SHOW FRONTEND CONFIG LIKE 'pattern'` | ✅ | |
| **Other** | `SHOW ENGINES` | ✅ | OLAP, MySQL, ES, Hive, Iceberg |
| | `SHOW PROC '/path'` | ✅ | Internal diagnostics |

---

## information_schema Tables

Complete list of all 58 tables in `information_schema`:

| # | Table | Category |
|---|-------|----------|
| 1 | analyze_status | Statistics |
| 2 | applicable_roles | Auth |
| 3 | be_bvars | Backend |
| 4 | be_cloud_native_compactions | Backend |
| 5 | be_compactions | Backend |
| 6 | be_configs | Backend |
| 7 | be_datacache_metrics | Backend |
| 8 | be_logs | Backend |
| 9 | be_metrics | Backend |
| 10 | be_tablet_write_log | Backend |
| 11 | be_tablets | Backend |
| 12 | be_threads | Backend |
| 13 | be_txns | Backend |
| 14 | character_sets | SQL Standard |
| 15 | cluster_snapshot_jobs | Backup |
| 16 | cluster_snapshots | Backup |
| 17 | collations | SQL Standard |
| 18 | column_privileges | Auth |
| 19 | column_stats_usage | Statistics |
| 20 | columns | Metadata |
| 21 | engines | Metadata |
| 22 | events | SQL Standard |
| 23 | fe_metrics | Frontend |
| 24 | fe_tablet_schedules | Frontend |
| 25 | fe_threads | Frontend |
| 26 | global_variables | Config |
| 27 | key_column_usage | SQL Standard |
| 28 | keywords | SQL Standard |
| 29 | load_tracking_logs | Load |
| 30 | loads | Load |
| 31 | materialized_views | Metadata |
| 32 | partitions | Metadata |
| 33 | partitions_meta | Metadata |
| 34 | pipe_files | Ingestion |
| 35 | pipes | Ingestion |
| 36 | recyclebin_catalogs | Metadata |
| 37 | referential_constraints | SQL Standard |
| 38 | routine_load_jobs | Ingestion |
| 39 | routines | SQL Standard |
| 40 | schema_privileges | Auth |
| 41 | schemata | Metadata |
| 42 | session_variables | Config |
| 43 | statistics | SQL Standard |
| 44 | stream_loads | Load |
| 45 | table_constraints | SQL Standard |
| 46 | table_privileges | Auth |
| 47 | tables | Metadata |
| 48 | tables_config | Metadata |
| 49 | tablet_reshard_jobs | Backend |
| 50 | task_runs | Tasks |
| 51 | tasks | Tasks |
| 52 | temp_tables | Metadata |
| 53 | triggers | SQL Standard |
| 54 | user_privileges | Auth |
| 55 | verbose_session_variables | Config |
| 56 | views | Metadata |
| 57 | warehouse_metrics | Warehouse |
| 58 | warehouse_queries | Warehouse |
