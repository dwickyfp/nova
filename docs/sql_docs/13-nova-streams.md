# Nova Streams

A Stream records the row changes of one table (its *source*) so a query or a
task can read what changed since it last consumed them. It is Snowflake's
table Stream: the Stream holds an offset, not a copy of the table, and each
Stream on a table has its own offset.

```sql
-- Standard (the default): inserts, updates and deletes.
CREATE STREAM analytics.orders_changes ON TABLE analytics.orders;

-- Append-only: inserts only, with its own offset.
CREATE STREAM analytics.orders_new ON TABLE analytics.orders APPEND_ONLY = TRUE;

-- Read the changes (does not consume them).
SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
FROM analytics.orders_changes;

-- Is anything pending?
SELECT SYSTEM$STREAM_HAS_DATA('analytics.orders_changes');

-- Consume: the offset moves only after this insert is verified.
INSERT INTO analytics.order_events
SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
FROM analytics.orders_changes;
```

How capture works, and the engine evidence behind it, is in
[stream-capture-feasibility](../reports/stream-capture-feasibility.md).

## Statements

| Statement | Behavior |
| --- | --- |
| `CREATE STREAM [IF NOT EXISTS] name ON TABLE source [APPEND_ONLY = TRUE \| FALSE]` | Registers the source (first Stream on it) and starts the Stream at the source's current state: no history is replayed. Standard unless `APPEND_ONLY = TRUE`. `IF NOT EXISTS` returns the existing Stream only when its source and mode match. |
| `DROP STREAM [IF EXISTS] name` | Destructive confirmation as usual. Owner role only. When a table's last Stream is dropped, Nova stops capturing it. |
| `SHOW STREAMS` | Streams of the session database whose source your role may read. |
| `DESCRIBE STREAM name`, `SHOW STREAM STATUS name` | Source, mode, owner, status, offset, head, pending commits. |
| `SHOW STREAM BACKLOG name` | Pending commits and row changes (before reduction), oldest pending commit time. |
| `SELECT … FROM stream` | The Stream's rows for the pending interval, joinable and filterable like a table, also in CTEs and subqueries. Never moves the offset. |
| `INSERT INTO target … SELECT … FROM stream` | Consumes: the offset moves to the interval read, after the insert is verified visible. |
| `SYSTEM$STREAM_HAS_DATA('name')` (also `NOVA_STREAM_HAS_DATA`) | TRUE when captured commits are pending. Never FALSE on doubt: a blocked or invalid Stream is an error. Like Snowflake, TRUE can be a commit whose changes cancel out (an insert then delete); consuming that interval still moves the offset. |

Names: with session database `analytics`, `s`, `analytics.s` and
`analytics.default.s` are the same Stream. A Stream cannot share a name with a
table or view.

## Rows and metadata

Every source column, then `METADATA$ACTION` (`INSERT` or `DELETE`),
`METADATA$ISUPDATE` and `METADATA$ROW_ID`. `SELECT *` includes the three
metadata columns, as in Snowflake.

Standard Streams return the **net** change per row between the offset and now:

| Since the offset | Rows |
| --- | --- |
| row inserted | `INSERT` (new values) |
| row deleted | `DELETE` (old values) |
| row updated (also an `INSERT` of an existing key) | `DELETE` old + `INSERT` new, both `ISUPDATE = TRUE`, same `ROW_ID` |
| inserted then deleted | nothing |
| inserted then updated | `INSERT` (final values) |
| updated, then back to the old values | nothing |
| deleted, then a new row with the same key | `DELETE` + `INSERT` with different `ROW_ID`s |

Append-only Streams return every insertion since the offset with the values as
inserted, including rows deleted again later; updates and deletes are not
returned. `METADATA$ROW_ID` is the row's lineage: the same across Streams on
one source and stable across updates.

## Consuming

* One consuming statement reads one Stream: `INSERT INTO target [(columns)] SELECT …`.
  `UPDATE`, `DELETE`, `MERGE`, `INSERT OVERWRITE` and `CREATE TABLE … AS` cannot
  read a Stream, and Streams cannot be consumed inside an explicit transaction.
* The insert runs with a label derived from the Stream offset. The offset moves
  only after the engine shows that insert visible, and each move must continue
  the previous one exactly (an unbroken chain of receipts). A failed insert
  does not move it; the next attempt retries the same interval.
* Two consumers of one Stream: one wins; the other is refused and can retry.
  Use one Stream per independent consumer.
* An insert whose outcome cannot be verified (a lost connection after the
  engine committed, with no load record to check) leaves the Stream blocked
  rather than risking a duplicate.

## Writing to a table that has Streams

Nova captures every write to a source that goes through Nova (worksheets,
Neva, the MySQL proxy, tasks, notebooks over the proxy): the change and its
before/after images are committed in one engine transaction by the stream
writer account, after your role's permission for that write is checked.

* Supported: `INSERT INTO t VALUES …` / `SELECT …` (all columns, in order or
  listed), `UPDATE t SET … [WHERE …]`, `DELETE FROM t [WHERE …]`. One write
  changes at most `STREAM_WRITE_MAX_ROWS` rows (50,000).
* Refused while the table has Streams: `INSERT OVERWRITE`, `TRUNCATE`,
  `ALTER TABLE`, partial column lists, `UPDATE … FROM`, `DELETE … USING`,
  labels, partitions, `DEFAULT`, and writes inside an explicit transaction.
* A write that reaches the table **without** Nova (a direct engine client,
  Stream Load, an external job) is detected from the engine's own version: the
  Stream becomes `BLOCKED` and every read or `HAS_DATA` call fails until the
  Stream is recreated. Dropping and recreating the table makes it
  `SOURCE_INVALID`. Nothing is silently missed.

## What a source can be

An internal, **unpartitioned Primary Key** table whose columns are integer,
`LARGEINT`, `DECIMAL`, `FLOAT`/`DOUBLE`, `BOOLEAN`, `CHAR`/`VARCHAR`/`STRING`,
`DATE` or `DATETIME`, with **no Ranger row filter or masking policy**. A Stream
shows historical (deleted, overwritten) values, which such policies cannot be
shown to govern, so Nova refuses rather than guess. Creating a Stream needs
`SELECT` on the source for your active role; reading it is checked again each
time, and the engine checks your `SELECT` in the same statement that reads it.

Not supported: Streams on views, external catalogs or partitioned tables,
`SHOW_INITIAL_ROWS`, `AT`/`BEFORE`, `CREATE OR REPLACE STREAM`.

## Tasks

`CREATE TASK … WHEN SYSTEM$STREAM_HAS_DATA('…')` without a `SCHEDULE` runs
when its Streams have data; see [the task manager](../08-task-manager.md#stream-triggered-tasks).

## Deployment

Streams need the stream writer account. Create it once with administrative
engine and Ranger settings:

```bash
cd backend && uv run python scripts/provision_stream_writer.py --write-env .env
```

It sets `STREAM_WRITER_USER`, `STREAM_WRITER_PASSWORD` and
`STREAM_WRITER_ROLE`. Nova then grants the writer role each source and its
journal through a Ranger policy (`nova-managed/stream-writer/<source>`) when
a Stream is created. Without the account, `CREATE STREAM` fails with
`STREAM_CHANGE_TRACKING_UNAVAILABLE`.

`SHOW STREAM LOAD` is StarRocks' own statement and unrelated to Nova Streams.
