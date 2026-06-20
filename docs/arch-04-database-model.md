# Architecture 04: Database Model — NOVA_SYSTEM

> All persistent state lives in StarRocks `NOVA_SYSTEM` database.
> No SQLite, no PostgreSQL. Nova is a pure StarRocks application.

---

## Architecture

```
┌─ nova.yaml + .env ─────────────────────────────────────┐
│  Storage credentials (static, git-versioned)            │
│  StarRocks connection (host, port, user, password)      │
└─────────────────────────────────────────────────────────┘

┌─ StarRocks: NOVA_SYSTEM ───────────────────────────────┐
│                                                          │
│  CONFIG schema     ← user-facing config (CRUD)          │
│  AUDIT schema      ← action logs                        │
│  STAGE schema      ← file manifests                     │
│  LINEAGE schema    ← data provenance                    │
│  QUALITY schema    ← table health                       │
│  USAGE schema      ← query analytics                    │
│                                                          │
└──────────────────────────────────────────────────────────┘

❌ SQLite — not needed
❌ PostgreSQL — not needed
```

---

## Schema: `NOVA_SYSTEM.CONFIG`

User-facing configuration data. Low volume, infrequent writes, frequent reads.

### STAGES

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG_STAGES (
    id                    VARCHAR(64) PRIMARY KEY,
    name                  VARCHAR(128) NOT NULL,
    database_name         VARCHAR(128) NOT NULL,
    schema_name           VARCHAR(128) NOT NULL,
    storage_connection    VARCHAR(128) NOT NULL,  -- ref ke nova.yaml key
    base_prefix           VARCHAR(512) NOT NULL,   -- "datalake/bronze/stage1"
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by            VARCHAR(128)
)
PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES(
    "replication_num" = "1",
    "enable_persistent_index" = "true"
);
```

**Example data:**

| id | name | database_name | schema_name | storage_connection | base_prefix |
|----|------|---------------|-------------|-------------------|-------------|
| abc-123 | stage1 | DATALAKE | bronze | production | datalake/bronze/stage1 |
| def-456 | stage1 | DATALAKE | silver | production | datalake/silver/stage1 |
| ghi-789 | imports | ANALYTICS | raw | backup | analytics/raw/imports |

**App-level constraints (enforced in Python):**
- Unique: `(database_name, schema_name, name)` — one stage name per schema
- `storage_connection` must exist in `nova.yaml`

---

### PINNED_QUERIES

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG_PINNED_QUERIES (
    id              VARCHAR(64) PRIMARY KEY,
    user_name       VARCHAR(128) NOT NULL,
    name            VARCHAR(256) NOT NULL,
    sql_text        TEXT NOT NULL,
    database_name   VARCHAR(128),
    schema_name     VARCHAR(128),
    is_shared       BOOLEAN DEFAULT FALSE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
)
PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES(
    "replication_num" = "1",
    "enable_persistent_index" = "true"
);
```

---

### USER_PREFERENCES

```sql
CREATE TABLE NOVA_SYSTEM.CONFIG_USER_PREFERENCES (
    user_name       VARCHAR(128),
    pref_key        VARCHAR(128),
    pref_value      TEXT,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_name, pref_key)
)
DISTRIBUTED BY HASH(user_name) BUCKETS 1
PROPERTIES(
    "replication_num" = "1",
    "enable_persistent_index" = "true"
);
```

**Common preferences:**

| pref_key | Example | Description |
|----------|---------|-------------|
| `default_database` | DATALAKE | Default database on login |
| `default_schema` | bronze | Default schema on login |
| `editor_theme` | dark | Monaco editor theme |
| `editor_font_size` | 14 | Editor font size |
| `results_page_size` | 100 | Rows per page in results |
| `sidebar_collapsed` | false | Sidebar state |

---

## Schema: `NOVA_SYSTEM.AUDIT`

See [arch-06-nova-system-database.md](./arch-06-nova-system-database.md) for full DDL.

---

## Entity Relationships

```
nova.yaml.storage_connections.production ──┐
nova.yaml.storage_connections.backup ──────┼── referenced by
nova.yaml.storage_connections.archive ─────┘   CONFIG.STAGES.storage_connection

CONFIG.STAGES (1) ──── (N) STAGE.FILE_MANIFEST (by stage_name)

CONFIG.PINNED_QUERIES ──── standalone (user-scoped)

CONFIG.USER_PREFERENCES ──── standalone (user-scoped)

AUDIT.LOG ──── standalone (append-only)

LINEAGE.LOAD_HISTORY ──── standalone (append-only)
```

---

## Initialization

```python
async def init_nova_system():
    """Auto-create NOVA_SYSTEM and all schemas/tables on startup."""
    
    schemas = ["CONFIG", "AUDIT", "STAGE", "LINEAGE", "QUALITY", "USAGE"]
    for schema in schemas:
        sr_execute(f"CREATE SCHEMA IF NOT EXISTS NOVA_SYSTEM.{schema}")
    
    # CONFIG tables
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STAGES (...)""")
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_PINNED_QUERIES (...)""")
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_USER_PREFERENCES (...)""")
    
    # Analytics tables
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_LOG (...)""")
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.STAGE_FILE_MANIFEST (...)""")
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.LINEAGE_LOAD_HISTORY (...)""")
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.QUALITY_TABLE_STATS (...)""")
    sr_execute("""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.USAGE_QUERY_STATS (...)""")
    
    print("✅ NOVA_SYSTEM initialized")
```

---

## CRUD via StarRocks Primary Key

```python
# Stage CRUD
class StageRepository:
    def create(self, stage: Stage):
        sr_execute(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STAGES VALUES (%s,%s,%s,%s,%s,%s,NOW(),%s)",
            [stage.id, stage.name, stage.database_name, stage.schema_name,
             stage.storage_connection, stage.base_prefix, stage.created_by]
        )
    
    def find(self, db: str, schema: str, name: str):
        return sr_execute(
            "SELECT * FROM NOVA_SYSTEM.CONFIG_STAGES WHERE database_name=%s AND schema_name=%s AND name=%s",
            [db, schema, name]
        )
    
    def delete(self, stage_id: str):
        sr_execute("DELETE FROM NOVA_SYSTEM.CONFIG_STAGES WHERE id=%s", [stage_id])


# Preference CRUD (upsert = INSERT INTO Primary Key table)
class PreferenceRepository:
    def set(self, user: str, key: str, value: str):
        sr_execute(
            "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES VALUES (%s,%s,%s,NOW())",
            [user, key, value]
        )
    
    def get(self, user: str, key: str):
        result = sr_execute(
            "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES WHERE user_name=%s AND pref_key=%s",
            [user, key]
        )
        return result['rows'][0][0] if result['rows'] else None
```
