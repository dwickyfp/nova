# Module 05: Table Manager

> Create, alter, and manage StarRocks tables — including columns, partitions, distribution, indexes, and properties.

---

## Table Types

| Type | Engine | Description |
|------|--------|-------------|
| **Primary Key** | OLAP | Unique key table, recommended for most use cases |
| **Duplicate Key** | OLAP | Allows duplicate rows, good for raw data |
| **Aggregate Key** | OLAP | Pre-aggregated data (SUM, MAX, MIN, REPLACE) |
| **Unique Key** | OLAP | Deprecated, use Primary Key instead |
| **External** | MySQL/JDBC/Hive/Elasticsearch | Virtual table pointing to external source |

---

## Create Table

### UI Form (Table Builder)

```
┌─ Create Table ──────────────────────────────────────────┐
│                                                          │
│  Database: [DATALAKE ▼]  Schema: [bronze ▼]             │
│  Table name: [payments                      ]           │
│  Table type: [Primary Key ▼]                            │
│  Comment: [Payment transactions           ]             │
│                                                          │
│  ── Columns ──                                           │
│  ┌──────────┬─────────────┬────────┬──────────────────┐ │
│  │ Name     │ Type        │ Key    │ Comment          │ │
│  ├──────────┼─────────────┼────────┼──────────────────┤ │
│  │ id       │ BIGINT      │ PK     │ Primary key      │ │
│  │ amount   │ DECIMAL(12,2)│       │ Payment amount   │ │
│  │ status   │ VARCHAR(20) │        │ Status           │ │
│  │ dt       │ DATE        │        │ Transaction date │ │
│  │ created  │ DATETIME    │        │ Created at       │ │
│  └──────────┴─────────────┴────────┴──────────────────┘ │
│  [+ Add Column]                                          │
│                                                          │
│  ── Partition ──                                         │
│  Type: [Range ▼]  Column: [dt ▼]                        │
│  ┌──────────────────────────────────────────────────┐   │
│  │ p202601: VALUES LESS THAN ("2026-02-01")        │   │
│  │ p202602: VALUES LESS THAN ("2026-03-01")        │   │
│  └──────────────────────────────────────────────────┘   │
│  [+ Add Partition]  [Auto Partition]                     │
│                                                          │
│  ── Distribution ──                                      │
│  Type: [Hash ▼]  Keys: [id ▼]  Buckets: [Auto ▼]       │
│                                                          │
│  ── Properties ──                                        │
│  replication_num: [3        ]                            │
│  storage_medium:  [HDD ▼]                               │
│                                                          │
│  [Show DDL Preview]  [Create Table]                      │
└──────────────────────────────────────────────────────────┘
```

### DDL Mode

Users can also write CREATE TABLE SQL directly in the SQL Worksheet.

---

## Alter Table

| Operation | UI Action | SQL |
|-----------|-----------|-----|
| Add column | Columns tab → [+ Add] | `ALTER TABLE ... ADD COLUMN ...` |
| Drop column | Columns tab → [Delete] | `ALTER TABLE ... DROP COLUMN ...` |
| Modify column | Columns tab → [Edit] | `ALTER TABLE ... MODIFY COLUMN ...` |
| Rename column | Columns tab → [Rename] | `ALTER TABLE ... RENAME COLUMN ...` |
| Add partition | Partitions tab → [+ Add] | `ALTER TABLE ... ADD PARTITION ...` |
| Drop partition | Partitions tab → [Delete] | `ALTER TABLE ... DROP PARTITION ...` |
| Modify properties | Properties tab → [Edit] | `ALTER TABLE ... SET (...)` |
| Rename table | Overview → [Rename] | `ALTER TABLE ... RENAME ...` |
| Swap table | Overview → [Swap] | `ALTER TABLE ... SWAP WITH ...` |
| Manual compaction | Overview → [Compact] | `ALTER TABLE ... COMPACT ...` |

---

## Table Properties (v4.1)

| Property | Description | Default |
|----------|-------------|---------|
| `replication_num` | Number of replicas | 3 |
| `storage_medium` | Storage tier (SSD/HDD) | HDD |
| `storage_cooldown_time` | Cooldown time for SSD | — |
| `enable_persistent_index` | Persistent index for Primary Key | true |
| `fast_schema_evolution` | Fast Schema Evolution v2 (shared-data) | false |
| `bloom_filter_columns` | Columns for bloom filter index | — |
| `colocate_with` | Colocation group | — |
| `enable_unique_key_merge_on_write` | Merge-on-write for Unique Key | true |
| `compression` | Compression codec | LZ4 |
| `partition_retention_condition` | Auto-drop old partitions | — |
| `datacache.enable` | Data cache for shared-data | true |
| `storage_volume` | Storage volume (shared-data) | default |

---

## Partitioning Types

| Type | Description | Example |
|------|-------------|---------|
| **Range** | Partition by date/numeric range | `PARTITION BY RANGE(dt)` |
| **Expression** | Partition by function expression | `PARTITION BY date_trunc('month', dt)` |
| **List** | Partition by discrete values | `PARTITION BY LIST(region)` |
| **Auto** | Automatic partition creation | `PARTITION BY RANGE(dt) ()` |

---

## Distribution Types

| Type | Description | When to use |
|------|-------------|-------------|
| **Hash** | Distribute by hash of key columns | Most cases |
| **Random** | Random distribution | When no good hash key |
| **Range** | Range-based (v4.1, shared-data) | Multi-tenant, auto-split |

---

## Index Types

| Index | Description | Create via |
|-------|-------------|-----------|
| **Bitmap** | Low-cardinality columns | `ALTER TABLE ... ADD INDEX ...` |
| **Bloom Filter** | High-cardinality equality | `bloom_filter_columns` property |
| **Inverted** | Text search / full-text | `ALTER TABLE ... ADD INDEX ... USING GIN` |
| **N-Gram BF** | Substring matching | `ALTER TABLE ... ADD INDEX ... USING NGRAM_BF` |
