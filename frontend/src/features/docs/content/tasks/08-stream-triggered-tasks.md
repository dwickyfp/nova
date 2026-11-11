---
title: Run a task when data changes
description: Make a task run by itself whenever a stream has new changes, and process each change once.
---

## What it is

A **stream-triggered task** has no schedule. Instead it watches a
[stream](/docs/tasks/streams) and runs shortly after new changes arrive in the
stream's table. Its body consumes the stream, so each change is processed once.

## Before you start

- Create the stream first. The task's owner role must be able to read it.
- Stream-triggered runs use the scheduling account an administrator connects
  to the owner role, like scheduled runs. Without it, each triggered run fails
  with "Run has no execution identity; start a new manual run or configure the
  owner role's scheduled execution account".

## Steps

1. Create a stream on the table you want to watch (skip this if it exists):

   ```sql
   CREATE STREAM orders_changes ON TABLE orders;
   ```

2. Create the task. Use `WHEN SYSTEM$STREAM_HAS_DATA('database.stream')` and
   no `SCHEDULE`, and make the body an `INSERT ... SELECT` from the stream:

   ```sql
   CREATE TASK apply_changes
     WHEN SYSTEM$STREAM_HAS_DATA('DOCS_QA_TASK.orders_changes')
   AS INSERT INTO order_events
      SELECT order_id, amount, METADATA$ACTION, METADATA$ISUPDATE, METADATA$ROW_ID
      FROM orders_changes;
   ```

   The result shows `schedule_kind` = `stream`. On the **Tasks** page the
   schedule reads **On stream data**.
3. Start it. Like every new task, it is created suspended:

   ```sql
   ALTER TASK apply_changes RESUME;
   ```

4. Change the table. Within about 15 seconds, Nova starts a run with trigger
   `stream`. You see it in the task's **Run history**.
5. To stop reacting to changes:

   ```sql
   ALTER TASK apply_changes SUSPEND;
   ```

You can still run the task by hand with `EXECUTE TASK apply_changes`. A manual
run consumes the stream in the same way:

```text
1 statement ran; 3 rows affected
```

## How it decides to run

- Nova checks the stream about every 15 seconds. When the stream has changes
  that this task has not yet run for, it starts one run.
- If the run consumes the stream, the stream is empty again and the task waits
  for the next change.
- If a run does **not** consume the stream (for example its body never reads
  it), the changes stay waiting, but the task does not run again for the same
  changes. It runs again when new changes arrive.
- If a run fails, it is not retried for the same changes. Fix the cause, then
  run the task by hand; it consumes everything still waiting.

## See what the trigger saw

`DESCRIBE TASK` shows each stream the task watches and its position:

```sql
DESCRIBE TASK apply_changes;
```

```text
property                          value
when                              SYSTEM$STREAM_HAS_DATA('DOCS_QA_TASK.orders_changes')
stream DOCS_QA_TASK.orders_changes  offset 3, head 5 (has data)
```

`offset` is the stream's bookmark and `head` the latest change. `has data`
means changes are waiting.

## Conditions with more than one stream

`WHEN` can combine stream checks with `AND`, `OR` and `NOT`:

```sql
CREATE TASK st_two
  WHEN SYSTEM$STREAM_HAS_DATA('orders_changes') OR SYSTEM$STREAM_HAS_DATA('orders_new')
AS INSERT INTO step_log SELECT 'two', NOW();
```

Each stream must already exist and be readable by your role. Other conditions
cannot be mixed in:

| You write | You get |
| --- | --- |
| `WHEN SYSTEM$STREAM_HAS_DATA('orders_changes') AND 1 = 1` | `A WHEN that uses SYSTEM$STREAM_HAS_DATA may combine stream calls only with AND, OR and NOT` |
| `WHEN SYSTEM$STREAM_HAS_DATA('nope')` | `WHEN reads stream DOCS_QA_TASK.nope: Stream does not exist or is not accessible` |

## A schedule plus a stream condition

If you add a `SCHEDULE` as well, the task is **not** stream-triggered. It runs
on its schedule, and the `WHEN` check decides at each start whether it does
anything: with no changes waiting, the run is skipped.

```sql
CREATE TASK st_sched
  WHEN SYSTEM$STREAM_HAS_DATA('orders_changes')
  SCHEDULE = '0 3 * * *'
AS INSERT INTO step_log SELECT 's', NOW();
```

The same is true for a task that runs `AFTER` another task: its stream
condition is checked when its turn comes. Only a root task with a stream
condition and no schedule is started by stream changes.

## Limits

- A stream that is stale or blocked makes its triggered task fail until the
  stream is recreated. See [Streams](/docs/tasks/streams).
