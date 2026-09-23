---
name: create-task
title: Schedule work with CREATE TASK
summary: Author a Nova CREATE TASK statement that is lowered to NOVA_SYSTEM metadata and run by the worker.
triggers: create task, schedule, jadwal, terjadwal, buat task, cron, pipeline, dag, finalize, orchestration
source: docs/sql_docs/08-native-starrocks-sql.md, docs/08-task-manager.md
---

# Skill: create-task

Author a `CREATE TASK` statement. It is **not** sent to StarRocks; Nova parses and
validates it, lowers it to `NOVA_SYSTEM.CONFIG_TASK*` metadata, and returns a
metadata row. The worker builds the engine's `SUBMIT TASK` per node at run time.

## Shape

```sql
CREATE TASK <name>
  [SCHEDULE = 'CRON <expr>' | 'INTERVAL <n> <unit>']
  [AFTER <parent_task>[, <parent_task>...]]
  [FINALIZE = <finalizer_task>]
  [WHEN = <condition>]
  [OVERLAP_POLICY = skip | queue | allow]
  AS <select-or-insert-statement>;
```

## Runtime semantics

- `AFTER` — normal dependency; a join waits for **all** parents.
- `FINALIZE` — not a dependency. Runs only after the whole graph succeeds, and is
  **skipped when the graph fails** (it is a task, not a callback).
- `WHEN` — false skips the node **and its descendants**; an evaluation error
  fails the node rather than silently skipping it.
- `OVERLAP_POLICY` — `skip` (default) rejects a new run while one is active;
  `queue` defers it; `allow` runs concurrently. Unknown values behave as `skip`.

## Template

```sql
CREATE TASK refresh_orders
  SCHEDULE = 'CRON 0 3 * * *'
  AS INSERT INTO analytics.order_facts SELECT * FROM NOVA_DEMO.orders;
```

## Caveats

- Nova returns a warning stating no statement was sent to StarRocks. Say so
  rather than implying the engine accepted DDL.
- The owner must have an active login session for the worker to run nodes
  (delegate-first RBAC); a task owned by a logged-out user fails with a clear
  error.
- Verify the schedule with `SELECT id, graph_id, state, overlap_policy FROM
  NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS;` — but you only author; the user runs.
