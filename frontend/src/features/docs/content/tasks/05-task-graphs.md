---
title: Task graphs
description: Chain tasks so they run in order, run steps side by side, skip steps on a condition, and add a final step.
---

## What a task graph is

A **task graph** is a set of tasks that run as one job, in an order you set.
For example: load raw data first, then clean two tables at the same time, then
publish when both are clean.

You build a graph with three clauses on `CREATE TASK`:

| Clause | Meaning |
| --- | --- |
| `AFTER parent[, parent...]` | Run this task after its parent task(s) succeed. With several parents, it waits for **all** of them. |
| `FINALIZE = root_task` | Run this task once at the very end, after the whole graph succeeds. |
| `WHEN condition` | Run this task only if the condition is true; otherwise skip it and everything after it. |

The first task (the one with no `AFTER`) is the **root**. The root's
schedule, stream condition and overlap policy apply to the whole graph.

## Build a graph

This graph loads, cleans two things side by side, publishes, and writes a
final log line. Run each statement in a worksheet with your database selected:

```sql
CREATE TASK load_raw AS INSERT INTO step_log SELECT 'load_raw', NOW();
CREATE TASK clean_a AFTER load_raw AS INSERT INTO step_log SELECT 'clean_a', NOW();
CREATE TASK clean_b AFTER load_raw AS INSERT INTO step_log SELECT 'clean_b', NOW();
CREATE TASK publish AFTER clean_a, clean_b AS INSERT INTO step_log SELECT 'publish', NOW();
CREATE TASK notify_done FINALIZE = load_raw AS INSERT INTO step_log SELECT 'finalize', NOW();
```

Create the parents before their children. A task named in `AFTER` or
`FINALIZE` must already exist and belong to your active role; otherwise you
get "Dependencies must exist and belong to the active owner role".

## Run the graph

Run a graph through its root:

```sql
EXECUTE TASK load_raw;
```

All five tasks run. `clean_a` and `clean_b` run at the same time, `publish`
waits for both, and `notify_done` runs last. The rows written by this run:

```text
step      at
load_raw  2026-11-11 09:41:55
clean_a   2026-11-11 09:41:57
clean_b   2026-11-11 09:41:57
publish   2026-11-11 09:42:00
finalize  2026-11-11 09:42:02
```

You cannot start a graph from the middle. Running a child gives:

```text
Task 'DOCS_QA_TASK.default.clean_a' runs after load_raw; EXECUTE TASK starts a graph at its root. EXECUTE TASK DOCS_QA_TASK.default.load_raw runs its graph.
```

The same rule applies to `ALTER TASK ... RESUME` and `SUSPEND`: use the root.

## See the graph

1. Open **Tasks** in the sidebar.
2. A graph shows as one row, named after its root. Click it.
3. The page shows the tasks as boxes joined by arrows, in the order they run.
   A final (`FINALIZE`) task has a dashed border. Each box shows the task's
   schedule and its last state.
4. Open **Run history** at the bottom and click a run to see the state of each
   task in that run.

## Skip steps with WHEN

`WHEN` takes a SQL condition. When it is false, the task is skipped, and so is
every task after it. The run still counts as a success.

```sql
CREATE TASK when_task WHEN 1 = 0 AS INSERT INTO step_log SELECT 'never', NOW();
EXECUTE TASK when_task;
```

The run succeeds and its step shows `skipped`; no row is written. If the
condition itself fails with an error, the task fails instead of being skipped.

A common condition is `SYSTEM$STREAM_HAS_DATA(...)`, which makes the task run
only when a table has changed. See
[Run a task when data changes](/docs/tasks/stream-triggered-tasks).

## What happens when a step fails

- A failed task fails the whole graph run. Tasks after it are skipped.
- Tasks that already succeeded keep their results; nothing is undone.
- The `FINALIZE` task runs only when the whole graph succeeds. It is skipped
  when anything fails.

## Remove a graph

Drop children before their parents. Dropping a parent that others still
depend on is refused:

```text
Tasks clean_a, clean_b, notify_done run after or finalize load_raw; drop them first
```

So remove the graph from the end:

```sql
DROP TASK notify_done;
DROP TASK publish;
DROP TASK clean_a;
DROP TASK clean_b;
DROP TASK load_raw;
```

## Limits

- All tasks in one graph belong to the same owner role.
- A schedule on a task that has `AFTER` is saved but not used. Only the root's
  schedule starts the graph.
- To change how tasks are linked, drop the tasks and create them again;
  `ALTER TASK` does not change links.
