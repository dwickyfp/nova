---
name: create-task
title: Schedule work with CREATE TASK
summary: Author a Nova CREATE TASK statement stored by Nova and run by the task worker.
triggers: create task, execute task, run task, resume task, suspend task, show tasks, describe task, stream task, jalankan task, schedule, jadwal, terjadwal, buat task, cron, pipeline, dag, finalize, orchestration, python task, multi statement
source: docs/sql_docs/08-native-starrocks-sql.md, docs/08-task-manager.md
---

# Skill: create-task

Author a `CREATE TASK` statement. It is **not** sent to StarRocks; Nova parses and
validates it, stores it as a Nova task, and returns the task's details. The worker builds the engine's `SUBMIT TASK` per node at run time.

## Shape

```sql
CREATE TASK <name>
  [AFTER <parent_task>[, <parent_task>...]]
  [FINALIZE = <finalizer_task>]
  [WHEN <condition>]
  [OVERLAP_POLICY = skip | queue | allow]
  [SCHEDULE = 'USING CRON <five-field-expr> [IANA-timezone]']
  [LANGUAGE SQL | PYTHON]
  [RUNTIME_VERSION = '3.12']            -- Python only
  [PACKAGES = ('name', 'name==1.2.3')]  -- Python only
  AS <body>;
```

The body is one of:

- one INSERT, CTAS or CACHE SELECT statement;
- a block of statements run in order, each ending with `;`:
  `AS BEGIN UPDATE ...; INSERT ...; END` (or the same statements between `$$` and `$$`);
- Python between `$$` and `$$` with `LANGUAGE PYTHON`.

Clause order is AFTER, FINALIZE, WHEN, OVERLAP_POLICY, SCHEDULE, LANGUAGE,
RUNTIME_VERSION, PACKAGES. Duplicate or out-of-order clauses are rejected. Cron strings may also be five bare fields;
a lone `CRON` prefix is invalid. Interval form is `SCHEDULE EVERY (INTERVAL 1 HOUR)`,
not `SCHEDULE = 'INTERVAL 1 HOUR'`. START is not supported. A bare AS SELECT is
not a task body; use INSERT SELECT, CTAS, or the supported CACHE SELECT form.

## Runtime semantics

- `AFTER` — normal dependency; a join waits for **all** parents.
- `FINALIZE` — not a dependency. Runs only after the whole graph succeeds, and is
  **skipped when the graph fails** (it is a task, not a callback).
- `WHEN` — false skips the node **and its descendants**; an evaluation error
  fails the node rather than silently skipping it.
- `OVERLAP_POLICY` — `skip` (default) rejects a new run while one is active;
  `queue` defers it; `allow` runs concurrently. Unknown values are rejected.

- A statement block runs as the run's user under the task's role, statement by
  statement; it stops at the first failure and keeps what already ran (no
  rollback). Blocks may read stages and use MERGE INTO. They may not create,
  drop or run tasks, switch roles, or control transactions. A block that
  updates, deletes or drops asks for confirmation once, when the task is created.
- A Python task runs in its own runtime with novaduck, like a notebook:
  `con = novaduck.connect()` reads every catalog the role sees, and
  `con.write`, `con.merge_into`, `con.put` write back. Packages must be on the
  approved package list; pandas, numpy, pyarrow, polars and DuckDB are already
  installed. What it prints is kept with the run.

## Start it

A new task is created **suspended**. Its schedule or stream trigger does not
run it until `ALTER TASK <root> RESUME`. `ALTER TASK <root> SUSPEND` stops it
again. Only the root task (or a standalone task) can be resumed or suspended.
After creating a scheduled or stream task, tell the user to resume it, or
resume it with approval. `SHOW TASKS [IN db.schema]` lists tasks with their
state, and `DESCRIBE TASK <name>` shows one task's details.

## Triggered by a Stream

`WHEN SYSTEM$STREAM_HAS_DATA('db.stream')` with no `SCHEDULE` runs the task
when the stream has data. Stream calls can only be combined with
AND/OR/NOT. The stream must already exist and be readable by the role. The
body should consume the stream with `INSERT … SELECT … FROM stream`.
Otherwise the task does not run again until new data arrives, and
DESCRIBE TASK warns `STREAM_NOT_CONSUMED`. Load `nova-streams` for Stream
rules.

## Run now

`EXECUTE TASK <name>` queues one run of the task's graph now, as the person who
runs it, under the task's owner role (which must be active). It returns once the
run is queued; the result shows in Tasks. Only a root or standalone task can be
executed; a task that runs after another is run through its root.

## Templates

```sql
CREATE TASK refresh_orders
  SCHEDULE = 'USING CRON 0 3 * * * Asia/Jakarta'
  AS INSERT INTO analytics.order_facts SELECT * FROM NOVA_DEMO.orders;

CREATE TASK sales.etl.close_day AS BEGIN
  UPDATE sales.etl.orders SET status = 'closed' WHERE order_date < CURRENT_DATE();
  INSERT INTO sales.etl.daily_totals
    SELECT order_date, SUM(amount) FROM sales.etl.orders GROUP BY order_date;
END;

CREATE TASK sales.etl.score_customers
  SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta'
  LANGUAGE PYTHON
  PACKAGES = ('scikit-learn')
AS $$
import novaduck
con = novaduck.connect()
df = con.sql("SELECT * FROM nova.sales.customers").df()
df["score"] = df["orders"] / df["orders"].max()
con.write(df, "nova.sales.customer_scores", mode="overwrite")
print(f"scored {len(df)} customers")
$$;

CREATE TASK sales.etl.orders_cdc
  WHEN SYSTEM$STREAM_HAS_DATA('sales.orders_stream')
  AS INSERT INTO sales.etl.order_events
     SELECT order_id, amount, METADATA$ACTION FROM sales.orders_stream;

ALTER TASK sales.etl.orders_cdc RESUME;

EXECUTE TASK sales.etl.close_day;
```

## Caveats

- A task needs a database: select one, or name it `database.schema.task`. A
  name is unique within its schema; `DROP TASK <name>` removes it (refused while
  other tasks run after it or while it is running) before creating it again.

- Nova notes that the task was saved and has not run yet. Say so rather than
  implying a run happened.
- A manual run (Run, or EXECUTE TASK) uses the session of the person who
  started it and fails if they sign out or switch roles first. Scheduled runs
  use the service account bound to the task's role.
- Draft without execution when requested. For explicit execution use query_mutate
  with approval. Inspect Tasks for the saved schedule and authorized run history;
  a successful DDL response proves metadata creation, not a completed task run.
