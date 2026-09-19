---
name: create-table
title: Create a StarRocks table
summary: Author valid CREATE TABLE DDL for a StarRocks 4.1.4 table, including keys, distribution and properties.
triggers: create table, buat tabel, new table, add table, ddl table, primary key table, duplicate key, aggregate table
source: docs/sql_docs/08-native-starrocks-sql.md
---

# Skill: create-table

Author `CREATE TABLE` DDL that StarRocks 4.1.4 accepts. Nova passes table DDL
through to the engine unchanged (no interception), so the statement must already
be valid StarRocks SQL. You write the DDL; you never run it — only a human runs
it.

## Rules

- The statement is **not** intercepted by Nova. It goes to StarRocks as written
  (after the guard and any `@stage` translation). Do not invent Nova-only clauses.
- Always qualify the table with its database: `db_name.table_name`.
- Prefer `IF NOT EXISTS` unless the user asked for a strict create.
- Every table needs a key model and a distribution strategy. Choose deliberately:
  - **Primary Key** (`PRIMARY KEY(col)`) — row-level updates/deletes; use for
    dimension/CRUD tables and low-volume config.
  - **Duplicate Key** (`DUPLICATE KEY(col)`) — append-only facts; use for
    immutable event/analytics data.
  - **Aggregate** (`AGGREGATE KEY(...)`) — pre-aggregated rollups.
- `DISTRIBUTED BY HASH(key) BUCKETS n` — pick a high-cardinality key; 4–16
  buckets is a reasonable default for dev.
- Set `PROPERTIES("replication_num" = "1")` for a single-node/dev cluster; omit
  on production clusters with >1 BE.
- Column types must be StarRocks types (`BIGINT`, `VARCHAR(n)`, `DATETIME`,
  `DECIMAL(p,s)`, `BOOLEAN`, …). Mark `NOT NULL` where the key requires it.

## Minimal template

```sql
CREATE TABLE IF NOT EXISTS <db>.<table> (
  <id_col>   BIGINT NOT NULL,
  <time_col> DATETIME NOT NULL,
  <payload>  VARCHAR(512)
) PRIMARY KEY(<id_col>)
DISTRIBUTED BY HASH(<id_col>) BUCKETS 4
PROPERTIES("replication_num" = "1");
```

## Variants

Append-only facts (Duplicate Key):

```sql
CREATE TABLE IF NOT EXISTS analytics.events (
  event_id   BIGINT NOT NULL,
  event_time DATETIME NOT NULL,
  payload    VARCHAR(512)
) DUPLICATE KEY(event_id)
DISTRIBUTED BY HASH(event_id) BUCKETS 8
PROPERTIES("replication_num" = "1");
```

Database first if needed:

```sql
CREATE DATABASE IF NOT EXISTS analytics;
```

## Notes and caveats

- `PRIMARY KEY` / `DISTRIBUTED BY` / `PROPERTIES` are StarRocks 4.x semantics.
  The exact option set is upstream-defined; treat anything beyond the shown
  clauses as needing the user's confirmation rather than inventing it.
- If the request needs a table type or clause you are not certain of, say so and
  offer the closest valid form instead of guessing.
- Cap the reply to the DDL plus a one-line rationale for the key/distribution
  choice. Do not execute it.
