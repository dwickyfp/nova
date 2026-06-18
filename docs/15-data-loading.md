# Module 15: Data Loading

> All data loading methods supported by StarRocks.

---

## Loading Methods Overview

| Method | Protocol | Mode | Source | Formats |
|--------|----------|------|--------|---------|
| **INSERT + FILES()** | MySQL | Sync | S3, HDFS, Azure, GCS, NFS | Parquet, ORC, CSV, Avro |
| **INSERT VALUES** | MySQL | Sync | Inline values | — |
| **INSERT SELECT** | MySQL | Sync | Internal/external tables | — |
| **Stream Load** | HTTP PUT | Sync | Local files, applications | CSV, JSON |
| **Broker Load** | MySQL | Async | S3, HDFS, Azure, GCS, NAS | CSV, JSON, Parquet, ORC |
| **Pipe** | MySQL | Async/Stream | S3, HDFS | Same as FILES() |
| **Routine Load** | MySQL | Async/Stream | Kafka | CSV, JSON, Avro |
| **Spark Load** | MySQL | Async | HDFS | CSV, Parquet, ORC |

---

## INSERT + FILES() (Recommended for Object Storage)

```sql
-- Query file directly
SELECT * FROM FILES('path'='s3://bucket/file.parquet', 'format'='parquet', creds);

-- CTAS: auto-create table from file
CREATE TABLE t AS SELECT * FROM FILES(...);

-- Load into existing table
INSERT INTO t SELECT * FROM FILES(...);

-- With strict mode (v4.1)
INSERT INTO t PROPERTIES('strict_mode'='true', 'max_filter_ratio'='0.1')
SELECT * FROM FILES(...);

-- Schema inference (DESC FILES)
DESC FILES('path'='s3://bucket/file.parquet', 'format'='parquet', creds);

-- Supported formats: Parquet, ORC, CSV, Avro
```

---

## Stream Load

```bash
# HTTP PUT to load local file
curl --location-trusted -u user:pass \
  -T file.csv \
  -H "label:load_001" \
  -H "column_separator:," \
  http://fe_host:8030/api/db/table/_stream_load

# JSON format
curl --location-trusted -u user:pass \
  -T file.json \
  -H "format:json" \
  -H "jsonpaths:["$.id","$.name"]" \
  http://fe_host:8030/api/db/table/_stream_load
```

---

## Broker Load

```sql
LOAD LABEL load_label
(
    DATA INFILE("s3://bucket/file.parquet")
    INTO TABLE target_table
    FORMAT AS "parquet"
)
WITH BROKER
(
    "aws.s3.endpoint" = "...",
    "aws.s3.access_key" = "...",
    "aws.s3.secret_key" = "..."
);
```

---

## Routine Load (Kafka)

```sql
CREATE ROUTINE LOAD my_load ON target_table
COLUMNS(id, name, amount)
PROPERTIES(
    "format" = "json",
    "max_batch_interval" = "10"
)
FROM KAFKA(
    "kafka_broker_list" = "broker:9092",
    "kafka_topic" = "my_topic",
    "kafka_partitions" = "0,1,2"
);
```

---

## Data Loading UI

### Stream Load (Upload)

```
┌─ Upload Data ───────────────────────────────────────────┐
│                                                          │
│  Target table: [orders ▼]                                │
│  File format:  [CSV ▼]                                   │
│                                                          │
│  Drop file here or [Browse]                              │
│                                                          │
│  Advanced:                                               │
│  Separator: [,      ]                                    │
│  Skip header: [1    ]                                    │
│  Strict mode: [✓]                                        │
│  Max filter ratio: [0.1 ]                                │
│                                                          │
│  [Upload & Load]                                         │
└──────────────────────────────────────────────────────────┘
```

### Broker Load

```
┌─ Broker Load ───────────────────────────────────────────┐
│                                                          │
│  Target table: [orders ▼]                                │
│  Source: [Stage: @stage1/incoming/orders.csv ▼]         │
│  Format: [CSV ▼]                                         │
│                                                          │
│  [Submit Load Job]                                       │
└──────────────────────────────────────────────────────────┘
```

### Load Job History

```
┌─ Load History ──────────────────────────────────────────┐
│                                                          │
│  Label           Table    Status   Rows     Duration     │
│  load_001        orders   ✅      12,400   3.2s         │
│  load_002        events   ✅      45,000   8.1s         │
│  load_003        orders   ❌      —        —            │
│    Error: column type mismatch                           │
│  broker_001      archive  ✅      1.2M     45s          │
└──────────────────────────────────────────────────────────┘
```
