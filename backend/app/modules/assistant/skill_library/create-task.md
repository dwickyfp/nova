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
  [AFTER <parent_task>[, <parent_task>...]]
  [FINALIZE = <finalizer_task>]
  [WHEN <condition>]
  [OVERLAP_POLICY = skip | queue | allow]
  [SCHEDULE = 'USING CRON <five-field-expr> [IANA-timezone]']
  AS <insert-or-ctas-or-cache-select-statement>;
```

Clause order is AFTER, FINALIZE, WHEN, OVERLAP_POLICY, SCHEDULE. Duplicate or
out-of-order clauses are rejected. Cron strings may also be five bare fields;
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

## Template

```sql
CREATE TASK refresh_orders
  SCHEDULE = 'USING CRON 0 3 * * * Asia/Jakarta'
  AS INSERT INTO analytics.order_facts SELECT * FROM NOVA_DEMO.orders;
```

## Caveats

- Nova returns a warning stating no statement was sent to StarRocks. Say so
  rather than implying the engine accepted DDL.
- The owner must have an active login session for the worker to run nodes
  (delegate-first RBAC); a task owned by a logged-out user fails with a clear
  error.
- Draft without execution when requested. For explicit execution use query_mutate
  with approval. Inspect Tasks for the saved schedule and authorized run history;
  a successful DDL response proves metadata creation, not a completed task run.
