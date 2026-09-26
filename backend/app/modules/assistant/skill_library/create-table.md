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
be valid StarRocks SQL. Draft when asked for SQL; use query_mutate with approval
only when the user asks to execute it.

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
  - **Unique Key** (`UNIQUE KEY(...)`) — replacement by key; a separate table
    model from Primary Key, with different storage/update behavior.
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

Daily expression partitioning automatically creates partitions while loading:

```sql
CREATE TABLE analytics.daily_events (
  event_date DATE NOT NULL,
  event_id BIGINT,
  quantity BIGINT
) DUPLICATE KEY(event_date, event_id)
PARTITION BY date_trunc('day', event_date)
DISTRIBUTED BY HASH(event_id) BUCKETS 8;
```

Expression partitioning uses `PARTITION BY date_trunc('day', column)` (or
another supported granularity). `PARTITION BY RANGE(column)` with
`START ... END ... EVERY ...` pre-creates a fixed range; it is not a substitute
for the requested expression form. Key and partition columns must satisfy the
selected table model. Do not invent date boundaries the user did not request.
See https://docs.starrocks.io/docs/table_design/data_distribution/expression_partitioning/.

## Notes and caveats

- For additional clauses, use `search_knowledge` with `syntax:CREATE TABLE`
  and the referenced grammar rule, then `validate_sql`. Ask for missing design
  requirements only when they affect the requested result.
- If the request needs a table type or clause you are not certain of, say so and
  offer the closest valid form instead of guessing.
- Cap the reply to the DDL plus a one-line rationale for the key/distribution
  choice. Do not execute a drafting request.
