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
- **DML:** SELECT, INSERT, UPDATE, DELETE, TRUNCATE
- **LOAD:** INSERT INTO ... SELECT FROM FILES(), INSERT INTO FILES() (unload)
- **ADMIN:** SHOW, DESCRIBE, EXPLAIN, SET, USE, KILL
- **Custom:** @stage syntax, BROWSE STAGE, DESCRIBE FILE, LOAD FROM STAGE
