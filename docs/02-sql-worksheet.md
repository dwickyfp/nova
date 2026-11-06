# Module 02: SQL Worksheet

> SQL editor with execution, results visualization, and @stage integration.

---

## Features

### Editor

| Feature | Description |
|---------|-------------|
| Monaco Editor | Full SQL editor with syntax highlighting |
| Multi-tab | Multiple worksheet tabs, preserved across sessions |
| Context switch | Database + Schema selector dropdown (sets USE context) |
| Auto-complete | Tables, columns, functions, @stage files |
| Format query | SQL formatting (sql-formatter) |
| Keyboard shortcuts | Cmd+Enter (run), Cmd+Shift+F (format), etc. |
| Find & Replace | Standard editor find/replace |
| Word wrap | Toggle word wrap |

### Execution

| Feature | Description |
|---------|-------------|
| Run selection | Execute selected text only |
| Run statement | Execute statement under cursor (detect `;` boundaries) |
| Run all | Execute entire worksheet sequentially |
| Cancel query | `KILL QUERY <id>` via backend |
| Execution time | Show elapsed time per query |
| Multi-statement | Execute multiple statements separated by `;` sequentially |

### Results

| Feature | Description |
|---------|-------------|
| Table view | Paginated result table |
| Column sort | Click column header to sort |
| Copy selection | Copy cells/rows/columns to clipboard |
| Export CSV | Download results as CSV |
| Export JSON | Download results as JSON |
| Row count | Show total rows returned |
| Truncation | Indicate when results are truncated (LIMIT) |
| Binary values | `VARBINARY`/`BINARY` cells show as text when they are UTF-8, otherwise as `0x` hex (`0xCAFE`), like the MySQL client |

### @stage Integration

| Feature | Description |
|---------|-------------|
| Stage autocomplete | Type `@` → suggest stages, then files |
| @stage rewrite | Transparently translate `@stage.file.ext` to `FILES()` |
| Rewrite indicator | Show translated SQL (collapsible) |
| Error on missing stage | Clear error if stage not found |

### Query History

| Feature | Description |
|---------|-------------|
| History list | All executed queries with timestamp |
| Re-run | Click to re-run historical query |
| Filter by status | Success / Failed |
| Filter by database | Filter by database context |
| Pin queries | Pin frequently used queries |

---

## @stage Syntax Reference

```sql
-- Basic: file in root of current stage
SELECT * FROM @stage1.data.csv;

-- With path: file in subfolder
SELECT * FROM @stage1.folder.subfolder.data.parquet;

-- Schema override (different schema, same database)
SELECT * FROM @silver.stage1.data.csv;

-- Full qualified (database.schema.stage.file)
SELECT * FROM @DATALAKE.bronze.stage1.data.json;

-- CTAS from stage
CREATE TABLE new_table AS SELECT * FROM @stage1.import.csv;

-- INSERT from stage
INSERT INTO existing SELECT * FROM @stage1.daily.parquet;

-- JOIN stage + table
SELECT a.*, b.name
FROM @stage1.transactions.csv a
JOIN customers b ON a.id = b.id;

-- Aggregate on stage
SELECT COUNT(*) FROM @stage1.events.parquet WHERE dt > '2026-01-01';

-- Export to stage
INSERT INTO @stage1.exports.backup.parquet SELECT * FROM orders;
```

---

## SQL Rewrite Flow

```
User writes:
  SELECT * FROM @stage1.data_pembayaran.csv LIMIT 10;

Backend rewrites:
  SELECT * FROM FILES(
      'path' = 's3://nova-stages/datalake/bronze/stage1/data_pembayaran.csv',
      'format' = 'csv',
      'csv.column_separator' = ',',
      'aws.s3.endpoint' = 'http://minio:9000',
      'aws.s3.access_key' = '...',
      'aws.s3.secret_key' = '...',
      'aws.s3.enable_path_style_access' = 'true',
      'aws.s3.enable_ssl' = 'false'
  ) LIMIT 10;

StarRocks executes rewritten SQL → returns results to UI
```

---

## Supported Statements

Nova SQL Worksheet supports ALL StarRocks SQL statements:

- **DDL:** CREATE/ALTER/DROP (TABLE, VIEW, MV, DATABASE, FUNCTION, PIPE, TASK, CATALOG, etc.)
- **DML:** SELECT, INSERT, UPDATE, DELETE, TRUNCATE, and Nova `MERGE INTO`
  (see [MERGE INTO](#merge-into))
- **LOAD:** INSERT INTO ... SELECT FROM FILES(), INSERT INTO FILES() (unload)
- **ADMIN:** SHOW, DESCRIBE, EXPLAIN, SET, USE, KILL
- **Custom:** @stage syntax, BROWSE STAGE, DESCRIBE FILE, LOAD FROM STAGE

## MERGE INTO

StarRocks 4.1.4 has no `MERGE`; Nova lowers it (`backend/app/sql_frontend/merge.py`)
so a Nova Primary Key table can be synchronised from any relation the caller can
read, including a Snowflake, PostgreSQL or remote StarRocks catalog:

```sql
MERGE INTO sales.orders AS t
USING sf_sales.SALES.ORDERS AS s            -- a table, view, or (subquery) AS s
ON t.order_id = s.ORDER_ID                  -- the target's whole primary key
WHEN MATCHED AND s.IS_PAID THEN UPDATE SET status = 'paid', amount = s.AMOUNT
WHEN MATCHED AND s.CANCELLED THEN DELETE
WHEN NOT MATCHED THEN INSERT (order_id, region, amount) VALUES (s.ORDER_ID, s.REGION, s.AMOUNT)
```

- **Target.** A Nova Primary Key table (`default_catalog`). External catalogs
  are read-only sources.
- **ON.** Equalities joined by `AND` that compare every primary key column of
  the target with a source value. Other conditions go in `WHEN ... AND`.
- **Clauses.** Tried in order; the first whose condition holds applies, and a
  row no clause selects is left unchanged. Only the last `WHEN MATCHED` and the
  last `WHEN NOT MATCHED` may omit `AND <condition>`. `WHEN NOT MATCHED` reads
  only the source. `UPDATE SET` cannot change a key column; `DEFAULT` gives a
  column its table default. `INSERT` without a column list fills the columns in
  table order (generated columns excepted); a column it omits gets its default
  or, for an auto-increment column, the next value.
- **Lowering.** Updates and inserts become one partial-update `INSERT INTO
  target (key, columns the clauses set) SELECT ... FROM source LEFT JOIN target
  ON ...`, so they are written completely or not at all. A matched row keeps
  every column no clause sets and a new row gets its defaults, so columns the
  MERGE never mentions (masked ones included) are never rewritten. Two source
  rows acting on the same key fail the statement before anything is written.
- **DELETE.** `WHEN MATCHED ... THEN DELETE` runs a key check and a `DELETE`
  before the upsert. Those are separate engine statements (best-effort): the
  engine has no multi-statement transaction for `DELETE` on this deployment.
  `DELETE` cannot be combined with `WHEN NOT MATCHED ... INSERT` in one MERGE.
- **Confirmation and audit.** A MERGE that can update or delete rows asks for
  confirmation like `UPDATE`. The audit row records the MERGE and the
  statements it ran; catalog lineage links the source tables to the target.
- **Tasks.** MERGE is not a `CREATE TASK` body.

