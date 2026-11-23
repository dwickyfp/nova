---
title: Streams
description: Track the rows that change in a table, read those changes, and process each change once.
---

## What a stream is

A **stream** watches one table (its **source**) and records every row that is
added, changed or removed through Nova. You can then ask "what changed since I
last looked?" and get just those rows.

A stream does not copy the table. It keeps a bookmark (its **offset**). Reading
the stream shows the changes after the bookmark. **Consuming** the stream,
by inserting its rows into another table, moves the bookmark forward, so the
same changes are not processed twice.

You work with streams in SQL. There is no streams screen; the
[Tasks](/docs/tasks/manage-and-monitor-tasks) page shows the tasks that use
them.

## Before you start

- [Prepare the task examples](/docs/tasks/example-tables) for the source and
  destination tables used below.
- The source must be a **Primary Key** table that is not partitioned, with
  simple column types: numbers, decimals, true/false, text, dates and
  date-times. It cannot have a row filter or masking policy, including one
  written for a name pattern that matches it (such as `ord*`) or a
  tag-based mask.
- Your active role needs `SELECT` on the source and `CREATE TABLE` in the
  database where the stream is created.
- The **first** stream on a table also needs `ALTER` on that table, because
  it changes how every write to the table works (see
  [Which writes are captured](#which-writes-are-captured)). If you can only
  read the table, ask its owner to create the first stream. Once a table has
  a stream, other roles can add their own streams with `SELECT` alone.
- Each role can own up to 100 streams, reading up to 20 different tables.
  Your administrator may set other limits.
- Select the database in your worksheet. In the examples the source is
  `orders`, a Primary Key table with `order_id`, `customer`, `amount` and
  `status`.

## Create a stream

```sql
CREATE STREAM orders_changes ON TABLE orders;
```

The result shows the stream, its source, its mode (`STANDARD`) and owner role.
A new stream starts at the table's current state: rows already in the table are
not changes, so the stream starts empty.

## Read the changes

Make some changes, then read the stream with `SELECT`:

```sql
INSERT INTO orders VALUES (4,'Dewi',50.00,'open');
UPDATE orders SET amount = 80.00 WHERE order_id = 2;
DELETE FROM orders WHERE order_id = 3;

SELECT order_id, customer, amount, status, METADATA$ACTION, METADATA$ISUPDATE
FROM orders_changes
ORDER BY order_id, METADATA$ACTION;
```

Each write shows the note "Captured for the table's streams." The stream
returns:

```text
order_id  customer  amount  status  METADATA$ACTION  METADATA$ISUPDATE
2         Budi      75.50   open    DELETE           1
2         Budi      80.00   open    INSERT           1
3         Citra     300.00  closed  DELETE           0
4         Dewi      50.00   open    INSERT           0
```

Besides the table's own columns, a stream has three extra columns:

| Column | Meaning |
| --- | --- |
| `METADATA$ACTION` | `INSERT` for a new row (or the new version of a changed row), `DELETE` for a removed row (or the old version of a changed row). |
| `METADATA$ISUPDATE` | True (1) when the row is half of an update: the old values as `DELETE` and the new values as `INSERT`. |
| `METADATA$ROW_ID` | A stable id for the row, the same across updates. |

Reading with `SELECT` never moves the bookmark. You can read, filter and join
the stream as often as you like. `SELECT *` includes the three extra columns.

A stream shows the **net** change per row since the bookmark. A row inserted
and then deleted before you read shows nothing; a row inserted and then updated
shows one `INSERT` with its final values.

## Is there anything new?

```sql
SELECT SYSTEM$STREAM_HAS_DATA('orders_changes') AS has_changes;
```

Returns 1 (true) when the stream has changes waiting, 0 (false) when not.
`NOVA_STREAM_HAS_DATA` is the same function under another name. Give it an
alias such as `AS has_changes`; without one, the column is named `TRUE` or
`FALSE`.

## Consume the changes

To process changes once, insert them into another table:

```sql
INSERT INTO order_events
SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
FROM orders_changes;
```

The result notes "Stream DOCS_QA_TASK.orders_changes consumed." The bookmark
moves past these changes, so the stream is now empty and
`SYSTEM$STREAM_HAS_DATA` returns 0.

- Only `INSERT INTO ... SELECT ... FROM stream` consumes a stream. If the
  insert fails, the bookmark does not move, and the next try reads the same
  changes.
- One statement consumes one stream.
- `UPDATE`, `DELETE`, `MERGE`, `INSERT OVERWRITE` and `CREATE TABLE ... AS`
  cannot read a stream.
- Each stream has its own bookmark. If two processes need the same changes,
  give each its own stream on the table.

## Kinds of streams

| Kind | Create with | Returns |
| --- | --- | --- |
| Standard (default) | `CREATE STREAM s ON TABLE t` | Inserts, updates and deletes, as the net change per row. |
| Append-only | `CREATE STREAM s ON TABLE t APPEND_ONLY = TRUE` | Only inserted rows, as they were inserted, even if they were later deleted. Updates and deletes are not shown. |
| With initial rows | `CREATE STREAM s ON TABLE t SHOW_INITIAL_ROWS = TRUE` | The first read returns every row the table held when the stream was created, as `INSERT`s, plus changes since. After the first consumption it returns changes only. |

```sql
CREATE STREAM orders_new ON TABLE orders APPEND_ONLY = TRUE;
CREATE STREAM orders_snapshot ON TABLE orders SHOW_INITIAL_ROWS = TRUE;
```

Initial rows are limited to 100,000.

## Which writes are captured

While a table has streams, Nova captures these writes made through Nova
(worksheets, tasks, notebooks and other Nova tools):

| Write | Shows in a standard stream as |
| --- | --- |
| `INSERT INTO t VALUES ...` or `INSERT INTO t SELECT ...` (all columns) | `INSERT` rows |
| `UPDATE t SET ... WHERE ...` | `DELETE` (old) + `INSERT` (new) pairs, `METADATA$ISUPDATE` = 1 |
| `DELETE FROM t WHERE ...` | `DELETE` rows |
| `MERGE INTO t ...` | Updates as pairs, new rows as `INSERT` |
| `TRUNCATE TABLE t` | A `DELETE` for every row |

For example, this `MERGE` updates order 1 and adds order 5:

```sql
MERGE INTO orders t USING order_updates u ON t.order_id = u.order_id
WHEN MATCHED THEN UPDATE SET amount = u.amount
WHEN NOT MATCHED THEN INSERT (order_id, customer, amount, status) VALUES (u.order_id, 'Eka', u.amount, 'open');
```

```text
order_id  amount  METADATA$ACTION  METADATA$ISUPDATE
1         120.00  DELETE           1
1         130.00  INSERT           1
5         60.00   INSERT           0
```

One write can change at most 50,000 rows of a table that has streams.

### Writes refused while a table has streams

| Statement | Message |
| --- | --- |
| `ALTER TABLE orders ADD COLUMN ...` | `ALTER TABLE on a table with streams is not supported; drop its streams first` |
| `INSERT INTO orders (order_id, amount) VALUES ...` | `An INSERT that does not list every column on a table with streams is not supported; drop its streams first` |
| `INSERT OVERWRITE orders ...` | `INSERT OVERWRITE on a table with streams is not supported; drop its streams first` |
| Any write, after a row filter or masking policy was added for the table | `Writes to <database>.orders are refused while it has streams: The source has a row filter or masking policy; ...` |

> **Warning:** A write that reaches the table **without** going through Nova
> (for example a load job run directly against the database engine) cannot be
> captured. Nova notices it, and the stream becomes **blocked**: reading it
> fails until you recreate the stream with `CREATE OR REPLACE STREAM`.

## Check a stream's health

List the streams in the current database:

```sql
SHOW STREAMS;
```

The `status` column is `READY` for a healthy stream and `STALE` for a stream
that waited too long (see below).

See one stream's details:

```sql
DESCRIBE STREAM orders_changes;
SHOW STREAM STATUS orders_changes;
```

Both return the same row: source, mode, owner role, `show_initial_rows`,
`retention_days`, `status`, `offset`, `head`, `pending_commits`, `has_data`,
`stale_after` and `source_status`. `offset` is the bookmark and `head` is the
latest change; when they differ, changes are waiting.

See how much is waiting:

```sql
SHOW STREAM BACKLOG orders_changes;
```

It returns `pending_commits` (how many writes), `pending_row_changes`,
`oldest_pending_commit_at` and `stale_after`. The row count is before netting,
so a standard stream may return fewer rows.

## Stale streams

Changes are kept for 14 days. If a stream has changes waiting and does not
consume them within 14 days of the oldest one, it becomes **stale**: reading it
and tasks that use it fail. `stale_after` in `DESCRIBE STREAM` says when that
would happen. A stream with nothing waiting never goes stale.

To use a stale stream again, recreate it. It starts again at the table's
current state:

```sql
CREATE OR REPLACE STREAM orders_snapshot ON TABLE orders;
```

## Remove a stream

```sql
DROP STREAM orders_changes;
DROP STREAM IF EXISTS orders_changes;
```

`DROP STREAM` asks you to confirm. Only the stream's owner role can drop or
replace it. When the last stream on a table is dropped, Nova stops capturing
that table, deletes the change history it kept for it, and the refused writes
above work again.

## Limits and errors

| Message | What it means |
| --- | --- |
| `Streams need a Primary Key table: row lineage and before images come from the primary key` | The source is not a Primary Key table. |
| `A table or view occupies this name` | A stream cannot have the same name as a table or view. |
| `A stream named … exists; a table, view or materialized view cannot take its name` | `CREATE TABLE`, `CREATE VIEW`, `CREATE MATERIALIZED VIEW`, `RECOVER TABLE` or a rename would give a table a stream's name. Choose another name, or drop the stream first. |
| `… is both a stream and a table or view; rename or drop one of them` | A table with the stream's name was created outside Nova. Nova will not guess which one you mean, so any query that names it, including one that would consume the stream, is refused until one of them is renamed or dropped. |
| `Only a role with ALTER on … can start capturing its changes` | The table has no stream yet, and your active role cannot alter it. Ask the table's owner to create the first stream. |
| `Creating a stream in … needs the CREATE TABLE privilege there` | Your active role cannot create objects in that database. Create the stream in a database where it can, or ask an administrator. |
| `Role … already owns … streams, the most allowed` | Your role reached its stream limit. Drop a stream you no longer use. |
| `Role …'s streams already read … tables, the most allowed` | Your role's streams already watch as many tables as allowed. Drop the streams of a table, or add the stream to a table your role already watches. |
| `Your … privilege could not be verified, so the stream was not created` | Nova could not confirm your privileges with the database. Try again; if it keeps happening, ask an administrator. |
| `Stream already exists with a different source or mode` | `CREATE STREAM IF NOT EXISTS` found a stream with that name on another table or in another mode. |
| `Use either OR REPLACE or IF NOT EXISTS, not both` | Pick one. |
| `The source has a row filter or masking policy; a stream would expose historical values those policies cannot govern` | A row filter or masking policy could apply to the table. Ask an administrator, or use another table. |
| `Streams can be read by SELECT and INSERT` | Another kind of statement, such as `CREATE TABLE ... AS SELECT`, tried to read a stream. |

Streams on views, external catalogs or partitioned tables are not supported.

Nova keeps each table's change history in tables whose names start with
`__nova_stream_`. Only Nova uses them: they do not appear in `SHOW TABLES` or
`information_schema` (only the `ACCOUNTADMIN` role still sees them), and SQL
that names one fails with
`Tables named __nova_stream_… are managed by Nova Streams and cannot be used
in SQL`. Read the changes through the stream instead.
