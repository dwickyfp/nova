---
title: Task and stream SQL reference
description: The syntax of every task and stream statement, with a short example of each.
---

## How to read the syntax

- Words in capitals are typed as shown.
- `[ ... ]` is optional. `a | b` means one or the other.
- `name` can be a plain name (the selected database is used) or
  `database.schema.name`. Nova databases have one schema, `default`.

## CREATE TASK

```text
CREATE TASK name
  [ AFTER parent_task [, parent_task ...] ]
  [ FINALIZE = root_task ]
  [ WHEN condition ]
  [ OVERLAP_POLICY = skip | queue | allow ]
  [ SCHEDULE EVERY (INTERVAL n SECOND | MINUTE | HOUR | DAY)
  | SCHEDULE = '[USING CRON] min hour day month weekday [time_zone]' ]
  [ LANGUAGE SQL | PYTHON ]
  [ RUNTIME_VERSION = '3.12' ]
  [ PACKAGES = ('package' [, 'package' ...]) ]
AS body
```

Clauses must appear in this order. The body is one of:

- one `INSERT` statement;
- `BEGIN statement; statement; ... END` or `$$ statement; statement; ... $$`;
- with `LANGUAGE PYTHON`, a Python script between `$$` and `$$`.

A new task is suspended. See [Create a task](/docs/tasks/create-a-task),
[Schedules](/docs/tasks/schedules),
[Multi-statement and Python tasks](/docs/tasks/multi-statement-and-python-tasks)
and [Task graphs](/docs/tasks/task-graphs).

```sql
CREATE TASK nightly_log
  SCHEDULE = 'USING CRON 0 3 * * * Asia/Jakarta'
AS INSERT INTO order_log SELECT NOW(), COUNT(*), SUM(amount) FROM orders;
```

## EXECUTE TASK

```text
EXECUTE TASK name
```

Queues one run now, as you, with the task's owner role. Only a root or
standalone task can be executed.

```sql
EXECUTE TASK log_orders;
```

## ALTER TASK

```text
ALTER TASK name RESUME | SUSPEND
```

`RESUME` starts the task's schedule or stream trigger; `SUSPEND` stops it.
Use the root task of a graph.

```sql
ALTER TASK log_orders RESUME;
ALTER TASK log_orders SUSPEND;
```

## DROP TASK

```text
DROP TASK [IF EXISTS] name
```

Removes a task. Asks for confirmation. Refused while the task has a run in
progress or while other tasks run after it. Run history is kept.

```sql
DROP TASK IF EXISTS log_orders;
```

## SHOW TASKS

```text
SHOW TASKS [IN database.schema]
```

Lists the tasks of the selected (or named) database that belong to your active
role.

```sql
SHOW TASKS IN DOCS_QA_TASK.default;
```

## DESCRIBE TASK

```text
DESCRIBE TASK name
```

Shows one task's properties and code, and for stream-triggered tasks the
position of each stream.

```sql
DESCRIBE TASK log_orders;
```

## CREATE STREAM

```text
CREATE [OR REPLACE] STREAM [IF NOT EXISTS] name
  ON TABLE source_table
  [ APPEND_ONLY = TRUE | FALSE ]
  [ SHOW_INITIAL_ROWS = TRUE | FALSE ]
```

`OR REPLACE` and `IF NOT EXISTS` cannot be used together. See
[Streams](/docs/tasks/streams).

```sql
CREATE STREAM orders_changes ON TABLE orders;
CREATE STREAM orders_new ON TABLE orders APPEND_ONLY = TRUE;
CREATE OR REPLACE STREAM orders_snapshot ON TABLE orders SHOW_INITIAL_ROWS = TRUE;
```

## DROP STREAM

```text
DROP STREAM [IF EXISTS] name
```

```sql
DROP STREAM IF EXISTS orders_changes;
```

## SHOW STREAMS

```text
SHOW STREAMS
```

Lists the streams of the selected database whose source your role can read,
with their mode and `status` (`READY` or `STALE`).

## DESCRIBE STREAM and SHOW STREAM STATUS

```text
DESCRIBE STREAM name
SHOW STREAM STATUS name
```

Both show the stream's source, mode, owner, status, offset, head, waiting
changes and when it would become stale.

## SHOW STREAM BACKLOG

```text
SHOW STREAM BACKLOG name
```

How many writes and row changes are waiting, and the time of the oldest.

## Reading and consuming a stream

```text
SELECT columns FROM stream ...                       -- read, does not move the bookmark
INSERT INTO target SELECT columns FROM stream ...    -- consume, moves the bookmark
```

Extra columns: `METADATA$ACTION`, `METADATA$ISUPDATE`, `METADATA$ROW_ID`.

```sql
INSERT INTO order_events
SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
FROM orders_changes;
```

## SYSTEM$STREAM_HAS_DATA

```text
SYSTEM$STREAM_HAS_DATA('stream')
NOVA_STREAM_HAS_DATA('stream')
```

True when the stream has changes waiting. Use it in `SELECT` or in a task's
`WHEN`.

```sql
SELECT SYSTEM$STREAM_HAS_DATA('orders_changes') AS has_changes;
```
