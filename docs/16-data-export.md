# Module 16: Data Export

> Export data from StarRocks to object storage.

---

## Export Methods

### INSERT INTO FILES (Recommended)

```sql
-- Export to multiple files
INSERT INTO FILES(
    'path' = 's3://bucket/export/',
    'format' = 'parquet',
    'compression' = 'zstd',
    creds
)
SELECT * FROM orders;

-- Export as single file
INSERT INTO FILES(
    'path' = 's3://bucket/export/orders.parquet',
    'format' = 'parquet',
    'single' = 'true',
    creds
)
SELECT * FROM orders;

-- Partitioned export
INSERT INTO FILES(
    'path' = 's3://bucket/export/',
    'format' = 'parquet',
    'compression' = 'lz4',
    'partition_by' = 'dt, region',
    creds
)
SELECT * FROM orders;

-- CSV export (v4.1)
INSERT INTO FILES(
    'path' = 's3://bucket/export/',
    'format' = 'csv',
    'csv.column_separator' = ',',
    'csv.include_header' = 'true',
    'csv.enclose' = '"',
    'csv.escape' = '"',
    creds
)
SELECT * FROM orders;

-- With compression (v4.1)
INSERT INTO FILES(
    'path' = 's3://bucket/export/',
    'format' = 'csv',
    'compression' = 'gzip',  -- gzip, snappy, zstd, lz4, deflate, zlib, bzip2
    creds
)
SELECT * FROM orders;
```

### Export Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `compression` | Compression codec | required |
| `single` | Export as single file | false |
| `target_max_file_size` | Max file size per file | 1GB |
| `partition_by` | Partition export by column | — |
| `csv.column_separator` | Column separator | `\t` |
| `csv.row_delimiter` | Row delimiter | `\n` |
| `csv.include_header` | Include header row | false |
| `csv.enclose` | Field enclosing character | — |
| `csv.escape` | Escape character | — |
| `parquet.version` | Parquet version | 1.0/2.0 |

### Export to @stage (Nova)

```sql
-- Export to stage (Nova translates to INSERT INTO FILES)
INSERT INTO @stage1.exports.backup.parquet
SELECT * FROM orders WHERE dt >= '2026-01-01';
```

---

## Export UI

```
┌─ Export Table ──────────────────────────────────────────┐
│                                                          │
│  Source table: [orders ▼]                                │
│  Destination:  [@stage1 ▼]  Path: [exports/    ]        │
│                                                          │
│  Format:      [Parquet ▼]                                │
│  Compression: [zstd ▼]                                   │
│  Mode:        (●) Multiple files  ( ) Single file        │
│  Partition by: [dt ▼]                                    │
│  Max file size: [1 GB  ]                                 │
│                                                          │
│  Optional WHERE:                                         │
│  [dt >= '2026-01-01'                       ]           │
│                                                          │
│  [Export]                                                │
│                                                          │
│  Result: ✅ 124 files exported, 2.3 GB total             │
│  Location: @stage1.exports/                              │
└──────────────────────────────────────────────────────────┘
```
