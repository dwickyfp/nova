# Architecture 06: NOVA_SYSTEM — Internal Metadata Database

> Single database for ALL Nova state: config + analytics.
> Similar to Snowflake's SNOWFLAKE database — exists in StarRocks, queryable via SQL.
> No SQLite, no PostgreSQL. Nova is a pure StarRocks application.

---

## Architecture

```
┌─ nova.yaml + .env ────────────────────────────────────┐
│  Storage credentials (static, git-versioned)           │
│  StarRocks connection                                  │
│  ⚠️ NEVER in database                                  │
└────────────────────────────────────────────────────────┘

┌─ StarRocks: NOVA_SYSTEM ──────────────────────────────┐
│                                                        │
│  CONFIG      ← user-facing config (CRUD, low volume)  │
│  AUDIT       ← every action logged (append-only)      │
│  STAGE       ← file inventory (per upload/delete)     │
│  LINEAGE     ← data provenance (per load)             │
│  QUALITY     ← table health snapshots (scheduled)     │
│  USAGE       ← query analytics (daily aggregation)    │
│                                                        │
│  All schemas auto-created on Nova startup.             │
│  CONFIG tables: Primary Key (supports UPDATE/DELETE)   │
│  Analytics tables: Duplicate Key (append-only)         │
│                                                        │
└────────────────────────────────────────────────────────┘
```

---

## Schema Overview

| Schema | Purpose | Write Pattern | Volume |
|--------|---------|---------------|--------|
| **CONFIG** | Stages, pinned queries, user preferences | CRUD (infrequent) | ~500 rows total |
| **AUDIT** | Every action through Nova | Append-only | ~10K rows/day |
| **STAGE** | File manifest per stage | On upload/delete | ~1K rows/stage |
| **LINEAGE** | Load provenance | Per load job | ~500 rows/day |
| **QUALITY** | Table health snapshots | Scheduled (6h) | ~100 rows/snapshot |
| **USAGE** | Aggregated query stats | Daily aggregation | ~50 rows/day |

---

## CONFIG Schema

### STAGES

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.STAGES (
    id                    VARCHAR(64) PRIMARY KEY,
    name                  VARCHAR(128) NOT NULL,
    database_name         VARCHAR(128) NOT NULL,
    schema_name           VARCHAR(128) NOT NULL,
    storage_connection    VARCHAR(128) NOT NULL,
    base_prefix           VARCHAR(512) NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by            VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### PINNED_QUERIES

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.PINNED_QUERIES (
    id              VARCHAR(64) PRIMARY KEY,
    user_name       VARCHAR(128) NOT NULL,
    name            VARCHAR(256) NOT NULL,
    sql_text        TEXT NOT NULL,
    database_name   VARCHAR(128),
    schema_name     VARCHAR(128),
    is_shared       BOOLEAN DEFAULT FALSE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### USER_PREFERENCES

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.USER_PREFERENCES (
    user_name       VARCHAR(128),
    pref_key        VARCHAR(128),
    pref_value      TEXT,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_name, pref_key)
) DISTRIBUTED BY HASH(user_name) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### AI_PROVIDERS (LLM Provider Connections)

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.AI_PROVIDERS (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,          -- "OpenAI", "Anthropic", "My vLLM"
    type            VARCHAR(32) NOT NULL,           -- openai, anthropic, openai_compatible
    endpoint        VARCHAR(512) NOT NULL,          -- https://api.openai.com/v1
    api_key_env     VARCHAR(128),                   -- env var name (never store actual key)
    default_params  TEXT,                            -- JSON: {"temperature": 0.7}
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### AI_MODELS (LLM/Embedding Models per Provider)

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.AI_MODELS (
    id              VARCHAR(64) PRIMARY KEY,
    provider_id     VARCHAR(64) NOT NULL,           -- FK to AI_PROVIDERS
    name            VARCHAR(128) NOT NULL,          -- "gpt-4o", "claude-sonnet-4"
    display_name    VARCHAR(256),                   -- "GPT-4o (128K context)"
    type            VARCHAR(32) NOT NULL,           -- llm, embedding
    max_tokens      INT DEFAULT 4096,
    default_params  TEXT,                            -- JSON: {"temperature": 0.7}
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR(128)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(provider_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### OBJECT_TAGS

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.OBJECT_TAGS (
    object_type     VARCHAR(32),
    object_name     VARCHAR(512),
    tag_key         VARCHAR(128),
    tag_value       VARCHAR(512),
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR(128),
    PRIMARY KEY (object_type, object_name, tag_key)
) DISTRIBUTED BY HASH(object_type) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### DASHBOARDS

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.DASHBOARDS (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    is_shared       BOOLEAN DEFAULT FALSE,
    created_by      VARCHAR(128),
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

### DASHBOARD_WIDGETS

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG.DASHBOARD_WIDGETS (
    id              VARCHAR(64) PRIMARY KEY,
    dashboard_id    VARCHAR(64),
    title           VARCHAR(256),
    sql_text        TEXT,
    chart_type      VARCHAR(32),
    x_axis          VARCHAR(64),
    y_axis          VARCHAR(64),
    position_x      INT,
    position_y      INT,
    width           INT DEFAULT 4,
    height          INT DEFAULT 3,
    refresh_seconds INT DEFAULT 300,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");
```

---

## AUDIT Schema

### LOG

```sql
CREATE TABLE NOVA_SYSTEM.AUDIT.LOG (
    log_id        BIGINT AUTO_INCREMENT,
    timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_name     VARCHAR(128),
    action        VARCHAR(64),
    target        VARCHAR(512),
    sql_text      TEXT,
    rewritten_sql TEXT,
    status        VARCHAR(16),
    duration_ms   INT,
    rows_affected BIGINT,
    error_msg     TEXT,
    client_ip     VARCHAR(45),
    session_id    VARCHAR(64)
)
PRIMARY KEY(log_id) DISTRIBUTED BY HASH(log_id) BUCKETS 4
PROPERTIES(
    "replication_num"="1",
    "partition_retention_condition"="timestamp >= date_sub(current_date(), INTERVAL 90 DAY)"
);
```

---

## STAGE Schema

### FILE_MANIFEST

```sql
CREATE TABLE NOVA_SYSTEM.STAGE.FILE_MANIFEST (
    file_id       BIGINT AUTO_INCREMENT,
    stage_id      VARCHAR(64),
    stage_name    VARCHAR(128),
    database_name VARCHAR(128),
    schema_name   VARCHAR(128),
    file_path     VARCHAR(1024),
    file_name     VARCHAR(256),
    file_size     BIGINT,
    format        VARCHAR(32),
    uploaded_by   VARCHAR(128),
    uploaded_at   DATETIME,
    last_queried  DATETIME,
    query_count   INT DEFAULT 0,
    etag          VARCHAR(128),
    is_deleted    BOOLEAN DEFAULT FALSE
)
PRIMARY KEY(file_id) DISTRIBUTED BY HASH(file_id) BUCKETS 2
PROPERTIES("replication_num"="1");
```

---

## LINEAGE Schema

### LOAD_HISTORY

```sql
CREATE TABLE NOVA_SYSTEM.LINEAGE.LOAD_HISTORY (
    load_id       BIGINT AUTO_INCREMENT,
    timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP,
    user_name     VARCHAR(128),
    source_type   VARCHAR(32),
    source_path   VARCHAR(1024),
    target_table  VARCHAR(256),
    rows_loaded   BIGINT,
    bytes_loaded  BIGINT,
    duration_ms   INT,
    status        VARCHAR(16),
    error_msg     TEXT,
    load_label    VARCHAR(256)
)
PRIMARY KEY(load_id) DISTRIBUTED BY HASH(load_id) BUCKETS 4
PROPERTIES(
    "replication_num"="1",
    "partition_retention_condition"="timestamp >= date_sub(current_date(), INTERVAL 365 DAY)"
);
```

---

## QUALITY Schema

### TABLE_STATS

```sql
CREATE TABLE NOVA_SYSTEM.QUALITY.TABLE_STATS (
    stat_id         BIGINT AUTO_INCREMENT,
    snapshot_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    table_fullname  VARCHAR(256),
    row_count       BIGINT,
    data_size_bytes BIGINT,
    column_count    INT,
    partition_count INT,
    null_pct        DOUBLE,
    duplicate_pct   DOUBLE,
    freshness_hours DOUBLE,
    avg_row_bytes   DOUBLE
)
PRIMARY KEY(stat_id) DISTRIBUTED BY HASH(stat_id) BUCKETS 2
PROPERTIES(
    "replication_num"="1",
    "partition_retention_condition"="snapshot_at >= date_sub(current_date(), INTERVAL 90 DAY)"
);
```

---

## USAGE Schema

### QUERY_STATS

```sql
CREATE TABLE NOVA_SYSTEM.USAGE.QUERY_STATS (
    stat_id            BIGINT AUTO_INCREMENT,
    date               DATE,
    user_name          VARCHAR(128),
    query_count        INT,
    success_count      INT,
    error_count        INT,
    total_rows_scanned BIGINT,
    total_rows_returned BIGINT,
    total_duration_ms  BIGINT,
    avg_duration_ms    DOUBLE,
    p50_duration_ms    DOUBLE,
    p95_duration_ms    DOUBLE,
    p99_duration_ms    DOUBLE,
    max_duration_ms    INT
)
PRIMARY KEY(stat_id) DISTRIBUTED BY HASH(stat_id) BUCKETS 2
PROPERTIES("replication_num"="1");
```

---

## Query Examples

### Config Queries

```sql
-- List all stages
SELECT name, database_name, schema_name, storage_connection, base_prefix
FROM NOVA_SYSTEM.CONFIG.STAGES
ORDER BY database_name, schema_name, name;

-- Saved queries for current user
SELECT name, sql_text, is_shared
FROM NOVA_SYSTEM.CONFIG.PINNED_QUERIES
WHERE user_name = 'analyst' OR is_shared = TRUE
ORDER BY created_at DESC;

-- User preferences
SELECT pref_key, pref_value
FROM NOVA_SYSTEM.CONFIG.USER_PREFERENCES
WHERE user_name = 'admin';
```

### Analytics Queries

```sql
-- Top users today
SELECT user_name, COUNT(*) AS actions,
       SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS errors
FROM NOVA_SYSTEM.AUDIT.LOG
WHERE DATE(timestamp) = CURDATE()
GROUP BY user_name ORDER BY actions DESC;

-- Data lineage: where did table data come from?
SELECT source_type, source_path, rows_loaded, timestamp
FROM NOVA_SYSTEM.LINEAGE.LOAD_HISTORY
WHERE target_table = 'DATALAKE.bronze.orders'
ORDER BY timestamp DESC;

-- Stale tables (>24h no load)
SELECT target_table, MAX(timestamp) AS last_load,
       TIMESTAMPDIFF(HOUR, MAX(timestamp), NOW()) AS hours_since
FROM NOVA_SYSTEM.LINEAGE.LOAD_HISTORY
WHERE status = 'SUCCESS'
GROUP BY target_table HAVING hours_since > 24;

-- Storage per stage
SELECT stage_name, COUNT(*) AS files, SUM(file_size)/1e9 AS total_gb
FROM NOVA_SYSTEM.STAGE.FILE_MANIFEST
WHERE is_deleted = FALSE
GROUP BY stage_name;

-- P95 query latency trend
SELECT date, AVG(p95_duration_ms) AS avg_p95_ms
FROM NOVA_SYSTEM.USAGE.QUERY_STATS
GROUP BY date ORDER BY date DESC LIMIT 30;
```

---

## UI Integration

### Hidden from Catalog Explorer

```python
EXCLUDED_DATABASES = {"NOVA_SYSTEM", "information_schema"}

def list_visible_databases():
    result = sr_execute("SHOW DATABASES")
    return [r for r in result['rows'] if r[0] not in EXCLUDED_DATABASES]
```

### Visible in Admin Analytics

Admin panel queries `NOVA_SYSTEM` tables directly for dashboards.

---

## Initialization

```python
async def init_nova_system():
    """Auto-create NOVA_SYSTEM on startup. Idempotent."""
    
    sr_execute("CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM")
    
    for schema in ["CONFIG", "AUDIT", "STAGE", "LINEAGE", "QUALITY", "USAGE"]:
        sr_execute(f"CREATE SCHEMA IF NOT EXISTS NOVA_SYSTEM.{schema}")
    
    # CONFIG (Primary Key tables)
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG.STAGES (...) PRIMARY KEY(id) ...")
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG.PINNED_QUERIES (...) PRIMARY KEY(id) ...")
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG.USER_PREFERENCES (...) ...")
    
    # Analytics (Duplicate Key tables)
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT.LOG (...) PRIMARY KEY(log_id) ...")
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.STAGE.FILE_MANIFEST (...) PRIMARY KEY(file_id) ...")
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.LINEAGE.LOAD_HISTORY (...) PRIMARY KEY(load_id) ...")
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.QUALITY.TABLE_STATS (...) PRIMARY KEY(stat_id) ...")
    sr_execute("CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.USAGE.QUERY_STATS (...) PRIMARY KEY(stat_id) ...")
    
    print("✅ NOVA_SYSTEM initialized (6 schemas, 8 tables)")
```

---

## Security

| Access | Rule |
|--------|------|
| Catalog Explorer | `NOVA_SYSTEM` hidden |
| SQL Worksheet | Can query `NOVA_SYSTEM` directly |
| Admin Analytics | Queries via backend API |
| Storage credentials | **NEVER** in `NOVA_SYSTEM` — in `nova.yaml` only |
