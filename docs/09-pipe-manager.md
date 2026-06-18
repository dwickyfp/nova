# Module 09: Pipe Manager

> Continuous data ingestion from object storage via StarRocks Pipe.

---

## Pipe Concepts

| Concept | Description |
|---------|-------------|
| **Pipe** | Continuous ingestion job monitoring a path for new/updated files |
| **AUTO_INGEST** | When TRUE, pipe monitors path and auto-loads new files |
| **Micro-batch** | Large jobs split into smaller sequential tasks |
| **Dedup** | Prevents duplicate loading (by file name + ETag/digest) |

---

## Operations

### Create Pipe

```sql
CREATE PIPE my_pipe
PROPERTIES (
    "AUTO_INGEST" = "TRUE",
    "POLL_INTERVAL" = "300",     -- seconds
    "BATCH_SIZE" = "1GB",
    "BATCH_FILES" = "256"
)
AS
INSERT INTO target_table
SELECT * FROM FILES(
    'path' = 's3://bucket/folder/*',
    'format' = 'parquet',
    <credentials>
);
```

### With @stage Syntax (Nova)

```sql
-- Nova translates @stage to FILES() automatically
CREATE PIPE my_pipe
PROPERTIES ("AUTO_INGEST" = "TRUE")
AS
INSERT INTO target_table
SELECT * FROM @stage1.incoming.*.parquet;
```

### Show Pipes

```sql
SHOW PIPES;
SHOW PIPES LIKE '%my_pipe%';
SHOW PIPES FROM my_database;
```

### Alter Pipe

```sql
ALTER PIPE my_pipe SUSPEND;
ALTER PIPE my_pipe RESUME;
ALTER PIPE my_pipe SET ("BATCH_SIZE" = "512MB");
```

### Drop Pipe

```sql
DROP PIPE my_pipe;
```

### Pipe File Status

```sql
-- Per-file ingestion status
SELECT * FROM information_schema.pipe_files
WHERE pipe_name = 'my_pipe';
```

---

## Pipe Manager UI

### Pipe List

```
┌─ Pipes ─────────────────────────────────────────────────┐
│                                                          │
│  [+ Create Pipe]                                         │
│                                                          │
│  Name          Status   Target Table   Files  Actions    │
│  orders_pipe   🟢 Auto  orders         247    [⏸][🗑]   │
│  events_pipe   🟢 Auto  events         1,842  [⏸][🗑]   │
│  backup_pipe   🟡 Susp  archive        12     [▶][🗑]    │
│  broken_pipe   🔴 Error staging        3      [▶][🗑]    │
└──────────────────────────────────────────────────────────┘
```

### Pipe Detail

```
┌─ Pipe: orders_pipe ─────────────────────────────────────┐
│                                                          │
│  Status: 🟢 Auto-ingesting                               │
│  Target: orders                                          │
│  Source: s3://[storage]/incoming/orders/*                 │
│  Poll: 300s  Batch: 1GB  Files/batch: 256                │
│                                                          │
│  ── File Status ──                                       │
│  orders_001.parquet    LOADED   2.3 MB   12,400 rows    │
│  orders_002.parquet    LOADED   2.1 MB   11,800 rows    │
│  orders_003.parquet    LOADING  2.4 MB   —              │
│  orders_bad.parquet    ERROR    1.8 MB   schema mismatch │
│                                                          │
│  Total: 247 files loaded, 1 loading, 1 error            │
└──────────────────────────────────────────────────────────┘
```

---

## Pipe Properties

| Property | Default | Description |
|----------|---------|-------------|
| `AUTO_INGEST` | TRUE | Auto-load new files |
| `POLL_INTERVAL` | 300s | How often to check for new files |
| `BATCH_SIZE` | 1GB | Data size per batch |
| `BATCH_FILES` | 256 | Files per batch |
