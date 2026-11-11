---
name: nova-streams
title: Explain, draft and run Nova Streams SQL
summary: Create, read, consume and drop Nova Streams (table change tracking) and trigger tasks from them, within their supported sources and limits.
triggers: nova streams, show_initial_rows, stale stream, create stream, drop stream, change tracking, change data capture, cdc, backlog, nova_stream_has_data, system$stream_has_data, metadata$action, buat stream, hapus stream, perubahan tabel
source: docs/sql_docs/13-nova-streams.md
---

# Skill: nova-streams

A Stream records the row changes of one table so a query or task can read what
changed since the Stream was last consumed. It works like a Snowflake table
Stream. Each Stream on a table keeps its own offset.

Report a Stream as created, read, consumed or dropped only from a successful
execution result. Loading this skill grants no permission to execute SQL.

## Names

With `analytics` as the active database, `orders_stream`,
`analytics.orders_stream` and `analytics.default.orders_stream` are the same
Stream. `default` is the only schema. A Stream cannot share a name with a table
or view, and `IF NOT EXISTS` does not bypass that or any access check.

## Examples

These examples use `analytics` as the session database.

```sql
CREATE STREAM analytics.orders_stream ON TABLE analytics.orders;
```

```sql
CREATE STREAM IF NOT EXISTS analytics.default.orders_stream
ON TABLE analytics.default.orders APPEND_ONLY = TRUE;
```

```sql
SHOW STREAMS;
```

```sql
DESCRIBE STREAM analytics.orders_stream;
```

```sql
SHOW STREAM STATUS analytics.orders_stream;
```

```sql
SHOW STREAM BACKLOG analytics.orders_stream;
```

```sql
SELECT SYSTEM$STREAM_HAS_DATA('analytics.orders_stream');
```

```sql
SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
FROM analytics.orders_stream;
```

```sql
INSERT INTO analytics.order_events
SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
FROM analytics.orders_stream;
```

```sql
CREATE OR REPLACE STREAM analytics.orders_stream ON TABLE analytics.orders
SHOW_INITIAL_ROWS = TRUE;
```

```sql
DROP STREAM IF EXISTS analytics.orders_stream;
```

## Behavior

- A Standard Stream (the default) returns the net change per row since its
  offset. An insert gives `INSERT`, a delete gives `DELETE` with the old values,
  and an update gives a `DELETE` and `INSERT` pair with `METADATA$ISUPDATE = TRUE`.
  An insert followed by a delete gives nothing. An append-only Stream returns
  every inserted row, including rows deleted later.
- A new Stream starts at the table's current state and replays no history.
- `SELECT … FROM stream` reads the changes without consuming them, and the result
  can be joined and filtered.
- `INSERT INTO target SELECT … FROM stream` consumes the changes. The offset
  moves only after the insert is verified. A failed insert leaves the offset
  where it was, so retrying reads the same changes. One consuming statement
  reads one Stream. `UPDATE`, `DELETE`, `MERGE`, `INSERT OVERWRITE` and
  `CREATE TABLE … AS` cannot read a Stream.
- `SYSTEM$STREAM_HAS_DATA` (or `NOVA_STREAM_HAS_DATA`) can be TRUE for changes
  that cancel out. On a blocked Stream it returns an error, never FALSE.
- Give each independent consumer its own Stream. When two consumers race on one
  Stream, one is refused and can retry.
- DROP is destructive and asks for confirmation. Only the owning role can drop.

## Sources and writes

- A source must be an internal, unpartitioned table with a primary key and
  simple column types (numbers, decimals, booleans, text, dates, datetimes).
  It must have no row filter or masking policy, and the user's role needs SELECT
  on it.
- While a table has Streams, Nova records every change made through Nova to that
  table: worksheets, Neva, tasks and the SQL proxy. Supported writes are full-row
  `INSERT`, `UPDATE … SET … WHERE …`, `DELETE … WHERE …`, `MERGE INTO` and
  `TRUNCATE TABLE` (which shows every row as a DELETE). A single write can change
  at most 50,000 rows.
- While a table has Streams, Nova refuses `ALTER TABLE`, `INSERT OVERWRITE`,
  partial column lists and writes inside an explicit transaction.
- When a write reaches the table outside Nova, the Stream becomes blocked, and
  reading it fails until the Stream is recreated. Explain this to the user
  instead of working around it.
- A Stream that is not consumed for 14 days after a change becomes stale and
  must be recreated with `CREATE OR REPLACE STREAM` (owner role) or dropped.
  `DESCRIBE STREAM` shows `stale_after`.
- `SHOW_INITIAL_ROWS = TRUE` makes the first read return the rows the table held
  at creation as INSERTs (up to 100,000 rows), then changes as usual.
- Do not write rows in `NOVA_SYSTEM` or send Stream syntax straight to the engine
  as a workaround. `AT`/`BEFORE` and Streams on views, external or partitioned
  tables are not supported.

## Tasks

A task with `WHEN SYSTEM$STREAM_HAS_DATA('…')` and no `SCHEDULE` runs when the
Stream has data. Like every new task, it starts suspended and runs only after
`ALTER TASK … RESUME`. Its body should consume the Stream with
`INSERT … SELECT … FROM stream`. Otherwise the run leaves the data pending, and
the task waits for new data before it runs again. `DESCRIBE TASK` warns about
this.

`SHOW STREAM LOAD` is a separate native StarRocks statement about load jobs and
has nothing to do with Nova Streams.
