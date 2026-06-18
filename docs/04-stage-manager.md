# Module 04: Stage Manager

> Snowflake-like stage abstraction: virtual folders backed by object storage.
> Users interact with stages only — storage backend is invisible.

---

## Concept

A **Stage** is a named folder bound to `database.schema`, backed by an object storage connection.

```
DATALAKE.bronze.stage1  →  minio://nova-stages/datalake/bronze/stage1/
DATALAKE.silver.stage1  →  minio://nova-stages/datalake/silver/stage1/
ANALYTICS.raw.import    →  azure://nova-backup/analytics/raw/import/
```

Users see: `@stage1.data.file.csv`
Admin configures: storage connection in `nova.yaml` once.
Stage definitions stored in: `NOVA_SYSTEM.CONFIG.STAGES`

---

## CRUD Operations

### Create Stage

```sql
-- Via UI (admin or authorized user)
Database: DATALAKE
Schema:   bronze
Name:     stage1
Storage:  [dropdown of configured connections]
```

**Under the hood:**
1. Register stage in `NOVA_SYSTEM.CONFIG.STAGES` (name, database, schema, storage_connection ref, base_prefix)
2. Create prefix "folder" in object storage (empty marker object)

### List Stages

```
USE DATALAKE.bronze;
-- UI shows stages in sidebar
```

### Drop Stage

```
Drop stage1 from DATALAKE.bronze
-- Confirmation: "This will delete all files in the stage"
-- Under the hood: delete all objects with prefix, then delete stage record
```

---

## File Operations

### Browse Files

```
┌─ @stage1 ──────────────────────────────────────┐
│                                                  │
│  📁 data/                                        │
│  📄 data_pembayaran.csv      2.3 MB  Jun 18     │
│  📄 orders.parquet           12 MB   Jun 17     │
│  📄 customers.json           890 KB  Jun 16     │
│  📁 archive/                                     │
│  📄 daily_report.csv         45 KB   Jun 18     │
│                                                  │
│  [Upload] [New Folder] [Refresh]                 │
└──────────────────────────────────────────────────┘
```

No storage-specific details shown. Pure file names, sizes, dates.

### Upload Files

```
┌─ Upload to @bronze.stage1 ──────────────────────┐
│                                                  │
│  Drop files here or [Browse]                     │
│                                                  │
│  📄 data.csv         2.3 MB  ✅                 │
│  📄 report.parquet   1.1 MB  ⏳                 │
│                                                  │
│  Target path: @stage1/          [Change ▼]      │
│                                                  │
│  [Upload]                                        │
│                                                  │
│  After upload, query with:                       │
│    SELECT * FROM @stage1.data.csv                │
└──────────────────────────────────────────────────┘
```

### Download Files

Click file → Download. Backend streams file from object storage to user browser.

### Delete Files

Click file → Delete → Confirmation.

### Create Subfolder

Click "New Folder" → Enter name → Creates prefix in object storage.

---

## Query Integration

### @stage Syntax

```sql
SELECT * FROM @stage1.file.csv
SELECT * FROM @stage1.path.to.file.parquet
SELECT * FROM @silver.stage1.file.csv
SELECT * FROM @DATALAKE.bronze.stage1.file.json
```

### CTAS from Stage

```sql
CREATE TABLE payments AS SELECT * FROM @stage1.data_pembayaran.csv;
```

### INSERT from Stage

```sql
INSERT INTO fact_payments SELECT * FROM @stage1.new_data.parquet;
```

### JOIN Stage + Table

```sql
SELECT a.*, b.name
FROM @stage1.transactions.csv a
JOIN dim_customers b ON a.cust_id = b.id;
```

### Export to Stage

```sql
INSERT INTO @stage1.exports.backup.parquet SELECT * FROM orders;
INSERT INTO @stage1.exports.partitioned.parquet
SELECT * FROM orders;  -- auto-partition by date
```

---

## Stage Access Control

Stages are **schema-bound** — access is controlled by StarRocks RBAC on the parent database/schema.

### Rule

```
User has SELECT on DATALAKE.bronze.*  →  Can read @bronze.stage1 files
User has INSERT on DATALAKE.bronze.*  →  Can upload to @bronze.stage1
User has NO access to DATALAKE.silver  →  Cannot see @silver.stage1 at all
```

### Access Matrix

| Privilege | Browse Files | Upload | Download | Query @stage | Delete |
|-----------|-------------|--------|----------|--------------|--------|
| `SELECT ON db.schema.*` | ✅ | ❌ | ✅ | ✅ | ❌ |
| `INSERT ON db.schema.*` | ✅ | ✅ | ✅ | ✅ | ❌ |
| `ALL ON db.schema.*` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ALL ON db.*` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ALL ON *.*` (ACCOUNTADMIN) | ✅ | ✅ | ✅ | ✅ | ✅ |
| No access to schema | ❌ Hidden | ❌ | ❌ | ❌ Access Denied | ❌ |

### Implementation

```python
# services/stage_access.py

def check_stage_access(session: UserSession, stage: Stage, action: str) -> bool:
    """
    Check if user has access to a stage based on StarRocks privileges.
    
    action: 'read' (browse, download, query) or 'write' (upload, delete)
    """
    conn = get_starrocks_connection(session)
    
    with conn.cursor() as cur:
        cur.execute("SHOW GRANTS FOR %s", [session.username])
        grants = str(cur.fetchall()).upper()
    
    db = stage.database_name.upper()
    schema = stage.schema_name.upper()
    
    # ACCOUNTADMIN bypasses all checks
    if "ALL ON *.*" in grants and "WITH GRANT OPTION" in grants:
        return True
    
    # Check privileges
    has_select = any_match(grants, [
        f"SELECT ON {db}.{schema}.*",
        f"SELECT ON {db}.*",
        f"ALL ON {db}.{schema}.*",
        f"ALL ON {db}.*",
        "ALL ON *.*",
    ])
    
    has_insert = any_match(grants, [
        f"INSERT ON {db}.{schema}.*",
        f"INSERT ON {db}.*",
        f"ALL ON {db}.{schema}.*",
        f"ALL ON {db}.*",
        "ALL ON *.*",
    ])
    
    if action == "read":
        return has_select
    elif action == "write":
        return has_insert
    
    return False
```

### UI Behavior

```
┌─ Catalog Explorer ──────────────────────────────────────┐
│                                                          │
│  DATALAKE (✅ user has access)                           │
│  ├── bronze (✅ SELECT)                                  │
│  │   ├── orders (table)                                  │
│  │   └── 📁 stage1 ← visible, can browse & query        │
│  └── silver (❌ no access)                               │
│      └── (hidden — user cannot see this schema)          │
│                                                          │
│  ANALYTICS (❌ no access)                                │
│  └── (entire tree hidden)                                │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### SQL Rewrite Guard

```python
# When rewriting @stage → FILES(), check access first

class StageRewriter:
    def rewrite(self, sql: str, context: SQLContext) -> str:
        stage_ref = self._parse_stage_ref(sql)
        
        if stage_ref:
            stage = self._find_stage(stage_ref)
            
            # Check access before rewriting
            if not check_stage_access(context.session, stage, "read"):
                raise SQLRewriteError(
                    f"Access denied: no SELECT on {stage.database_name}.{stage.schema_name}"
                )
            
            return self._rewrite_to_files(sql, stage)
        
        return sql
```

---

## SQL Rewrite Rules

| User writes | Backend rewrites to |
|-------------|-------------------|
| `@stage1.file.csv` | `FILES('path'='...stage1/file.csv', 'format'='csv', creds...)` |
| `@stage1.data.file.parquet` | `FILES('path'='...stage1/data/file.parquet', 'format'='parquet', creds...)` |
| `@silver.stage1.file.json` | `FILES('path'='...silver/stage1/file.json', 'format'='json', creds...)` |

Format auto-detected from file extension:
- `.csv` / `.tsv` → `csv`
- `.json` / `.jsonl` → `json`
- `.parquet` / `.pq` → `parquet`
- `.orc` → `orc`
- `.avro` → `avro`

CSV auto-params: `csv.column_separator=','`, `csv.row_delimiter='\n'` (if not specified).

---

## Storage Provider Abstraction

Stage files are stored via pluggable providers. Users never see provider details.

| Provider | Backend | Credential Type |
|----------|---------|----------------|
| MinIO | S3 protocol | Access Key + Secret Key |
| AWS S3 | S3 protocol | Access Key + Secret Key, IAM Role, Instance Profile |
| Azure Blob | Azure SDK | Shared Key, SAS Token, Managed Identity |
| Google GCS | GCS SDK | Service Account, Compute Engine SA |
| Alibaba OSS | S3 protocol | Access Key + Secret Key |

Provider determines how `FILES()` params are generated. Stage layer is identical regardless of provider.
