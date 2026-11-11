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
| `CREATE [OR REPLACE] STREAM [IF NOT EXISTS] name ON TABLE source [APPEND_ONLY = TRUE \| FALSE] [SHOW_INITIAL_ROWS = TRUE \| FALSE]` | Registers the source (first Stream on it) and starts the Stream at the source's current state. Standard unless `APPEND_ONLY = TRUE`. `IF NOT EXISTS` returns the existing Stream only when its source and mode match. `OR REPLACE` (owner role only) replaces an existing or stale Stream with a new one at a fresh offset; it cannot be combined with `IF NOT EXISTS`. |
| `DROP STREAM [IF EXISTS] name` | Destructive confirmation as usual. Owner role only. Works on stale Streams too. When a table's last Stream is dropped, Nova stops capturing it. |
| `SHOW STREAMS` | Streams of the session database whose source your role may read, with their status (`READY` or `STALE`). |
| `DESCRIBE STREAM name`, `SHOW STREAM STATUS name` | Source, mode, owner, status, offset, head, pending commits, `show_initial_rows`, `retention_days` and `stale_after`. |
| `SHOW STREAM BACKLOG name` | Pending commits and row changes (before reduction), oldest pending commit time, `stale_after`. |
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

### `SHOW_INITIAL_ROWS = TRUE`

The Stream's first read returns every row the table held when the Stream was
created, as `INSERT` rows, netted with what changed since (a row updated after
creation comes back once, with its new values; a row deleted since does not
come back). After the first consumption the Stream returns changes only, as
usual. Nova takes the snapshot when the Stream is created, under the source's
lock, as a journal commit only this Stream reads; the table itself is not
written. The snapshot holds at most `STREAM_READ_MAX_ROWS` rows (100,000).

### Staleness

Unconsumed changes are kept for `STREAM_RETENTION_DAYS` (14 days, Snowflake's
longest extension). A Stream whose oldest unconsumed change is older becomes
`STALE`: reads, `SYSTEM$STREAM_HAS_DATA` and triggered tasks fail with
`STREAM_STALE`, its changes are released for collection, and it must be
recreated (`CREATE OR REPLACE STREAM`) or dropped. A Stream with nothing pending
never goes stale. `stale_after` in `DESCRIBE STREAM` says when it would.

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
  listed), `UPDATE t SET … [WHERE …]`, `DELETE FROM t [WHERE …]`,
  `MERGE INTO t …` and `TRUNCATE TABLE t`. One write changes at most
  `STREAM_WRITE_MAX_ROWS` rows (50,000).
* `TRUNCATE` is journaled as the deletion of every row, so Standard Streams
  show each pre-existing row as a `DELETE` (Snowflake's behavior).
* `MERGE` writes whole rows: a matched row keeps the columns no clause sets
  and a new row gets the table default for them, exactly as an ordinary
  MERGE. A MERGE with `WHEN MATCHED … THEN DELETE` and an update is two commits
  under one lock (a delete, then the upsert), both computed before either is
  written. Like an ordinary MERGE of that shape it is not one transaction: a
  read in between can see the delete before the upsert, and a failure of the
  upsert keeps the delete. Each commit is captured exactly.
  A target with generated columns cannot be merged while it has Streams.
* Refused while the table has Streams: `INSERT OVERWRITE`, `ALTER TABLE`,
  partial column lists, `UPDATE … FROM`, `DELETE … USING`, labels, partitions
  (including `TRUNCATE … PARTITION`), `DEFAULT`, and writes inside an explicit
  transaction.
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

Not supported: Streams on views, external catalogs or partitioned tables, and
`AT`/`BEFORE`. Partitioned tables stay out because a captured commit can move
several partitions' versions at once and the engine cannot say which ones a
commit touched, so a direct write to another partition at the same moment could
be absorbed unnoticed; Nova refuses rather than weaken "nothing is missed".

## Tasks

`CREATE TASK … WHEN SYSTEM$STREAM_HAS_DATA('…')` without a `SCHEDULE` runs
when its Streams have data; see [the task manager](../08-task-manager.md#stream-triggered-tasks).

## API and metrics

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/streams?database=` | Streams the active role may read, with mode and status |
| `GET /api/v1/streams/{database}/{name}` | `DESCRIBE STREAM` |
| `GET /api/v1/streams/{database}/{name}/backlog` | `SHOW STREAM BACKLOG` |
| `GET /api/v1/streams/{database}/{name}/consumptions` | Recent consumptions: offsets moved, target, state, principal. Never SQL or rows |

`404` means missing or not readable (the same answer), `410` stale.

Metrics (Prometheus, no per-stream labels): `nova_stream_capture_commits_total{kind}`,
`nova_stream_capture_rows_total`, `nova_stream_capture_failures_total{code}`,
`nova_stream_capture_duration_seconds`, `nova_stream_source_blocks_total{status}`,
`nova_stream_reads_total{result}`, `nova_stream_read_duration_seconds`,
`nova_stream_reducer_rows_total{side}`, `nova_stream_consumptions_total{result}`,
and, refreshed by the scheduler's maintenance pass every
`STREAM_MAINTENANCE_INTERVAL_SECONDS` (300 s): `nova_streams{status}`,
`nova_stream_sources{status}`, `nova_stream_backlog_commits_max`,
`nova_stream_retention_seconds_remaining_min`, `nova_stream_stale_total`.
Task triggers add `nova_stream_trigger_runs_total`,
`nova_stream_trigger_errors_total{code}` and `nova_stream_tasks_not_consuming`.
Alert on any `nova_stream_source_blocks_total` increase, on
`nova_stream_retention_seconds_remaining_min` below a day, and on
`nova_stream_tasks_not_consuming` above zero.

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
