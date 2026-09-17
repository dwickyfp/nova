# Module 08: Task Manager

> Manage asynchronous ETL tasks: SUBMIT TASK, ALTER TASK, task runs, scheduling.

---

## Task Concepts

| Concept | Description |
|---------|-------------|
| **Task** | Template for an async ETL job (INSERT, CTAS) |
| **TaskRun** | Single execution instance of a task |
| **Schedule** | One-shot or periodic (SCHEDULE EVERY) |

### Task States

Verified against a live StarRocks 4.1.1 engine (NOVA-23, 2026-09-17):

> **There is no `STATE` column on `information_schema.tasks`.** Its columns are
> `TASK_NAME`, `CREATE_TIME`, `SCHEDULE`, `CATALOG`, `DATABASE`, `DEFINITION`,
> `EXPIRE_TIME`, `PROPERTIES`, `CREATOR`. The `ACTIVE` / `PAUSE` values below were
> never observable, and `SHOW TASKS` is not a statement in 4.1.1 either
> (`No viable statement for input 'SHOW TASKS'`). Suspend/resume state is **not
> queryable** through a documented surface; infer it from the `SCHEDULE` string and
> your own bookkeeping.

| State | Description |
|-------|-------------|
| `ACTIVE` | Task is registered and ready — **not a queryable column** |
| `PAUSE` | Task is suspended — **not a queryable column** |

Suspended tasks: `ALTER TASK ... SUSPEND` stops future scheduled runs and
`... RESUME` restarts them (verified: run count stayed flat for 25 s after SUSPEND,
then advanced after RESUME).

### TaskRun States

| State | Description |
|-------|-------------|
| `PENDING` | Waiting in queue |
| `RUNNING` | Currently executing |
| `SUCCESS` | Completed successfully |
| `FAILED` | Execution failed |
| `MERGED` | Merged with newer pending task |
| `SKIPPED` | No data changes detected (MV refresh) |

---

## Operations

### Create Task

```sql
-- One-shot task
SUBMIT TASK etl_step1 AS
INSERT INTO staging SELECT * FROM raw_data;

-- Periodic task
SUBMIT TASK etl_hourly
SCHEDULE EVERY(INTERVAL 1 HOUR)
AS INSERT INTO agg_table SELECT * FROM staging;

-- With start time
SUBMIT TASK etl_daily
SCHEDULE START('2026-01-01 00:00:00') EVERY(INTERVAL 1 DAY)
AS INSERT OVERWRITE agg_table SELECT * FROM staging;
```

**Verified schedule surface (live 4.1.1, NOVA-23):** the only accepted schedule
forms are `MANUAL` (omit the clause), `SCHEDULE EVERY(INTERVAL …)`, and
`SCHEDULE START('<literal>') EVERY(INTERVAL …)`.

**There is no cron support.** `SCHEDULE = 'USING CRON 0 * * * * UTC'` is rejected
with `Unexpected input '='`; the parser only knows `EVERY` and `START`. Cron — the
user's primary requested trigger — therefore has **no native StarRocks primitive**
and must be implemented by Nova. `START` also requires a single-quoted literal, not
an expression: `START(CURRENT_TIMESTAMP + INTERVAL 15 SECOND)` is rejected.

`START` literals are interpreted in the **session/engine timezone** (`@@time_zone`,
default `Asia/Jakarta`), not UTC — verified: a literal built from the local wall
clock fired at the expected wall-clock second, while an equal UTC literal fired
7 h later. Any Nova scheduler must pin the timezone explicitly rather than assume
UTC.

### Alter Task (v4.1)

```sql
-- Suspend
ALTER TASK etl_task SUSPEND;

-- Resume
ALTER TASK etl_task RESUME;

-- Update properties
ALTER TASK etl_task SET ('session.query_timeout' = '5000');

-- With IF EXISTS
ALTER TASK IF EXISTS etl_task SUSPEND;
```

### Drop Task

```sql
DROP TASK etl_task;
DROP TASK IF EXISTS etl_task;
DROP TASK etl_task FORCE;  -- force drop pipe-internal tasks (v4.1)
```

### Authorization model (verified, NOVA-23)

Privileges are checked **at `SUBMIT TASK` time, against the submitting user's
identity** — not at run time and not against a service account. Verified:

- An unprivileged user (`'nova_limited'@'%'`, no grants) submitting a task whose
  body needs `INSERT` on `dst` is rejected immediately:
  `Access denied; you need (at least one of) the INSERT privilege(s) on TABLE dst`.
  The task row is never created.
- Granting that same `INSERT` privilege makes the identical `SUBMIT TASK` succeed.
- The submitter is recorded verbatim: `information_schema.tasks.CREATOR` holds
  `'nova_limited'@'%'`.

This is good news for Nova: **the engine already enforces RBAC on task definition**,
so a Nova DAG layer that creates each task under the requesting user's connection
inherits that enforcement for free. Note the corollary — the run executes with the
creator's identity, so a privilege revoked after `SUBMIT` is not re-checked at run
time.

The engine also **filters reads by the caller's privileges**: a user with no grants
on the task's database sees an empty `information_schema.tasks`, while root sees the
row. Nova nonetheless leaks all tasks, because `TaskService._connect()`
(`backend/app/modules/tasks/service.py:32-38`) opens a **root** connection with
`STARROCKS_ROOT_USER` (`:36`) and the router passes only `get_current_user` for
authentication (`backend/app/modules/tasks/router.py:38,51`) — the user is never
threaded into the query. This is a pre-existing backend defect, not a StarRocks
limitation, and it is why the read path shows every task to every signed-in user.
Fix is mechanical: connect as the calling user via `get_user_connection`
(`backend/app/core/deps.py:75`), the way `users` and `resource_groups` already do.

### Show Tasks

```sql
-- List all tasks
SELECT * FROM information_schema.tasks;

-- Task run history
SELECT * FROM information_schema.task_runs;

-- Task run details (MV-specific)
SELECT
    TASK_NAME,
    CREATE_TIME,
    get_json_string(EXTRA_MESSAGE, '$.refreshMode') AS refresh_mode,
    get_json_string(EXTRA_MESSAGE, '$.mvPartitionsToRefresh') AS mv_partitions
FROM information_schema.task_runs
WHERE TASK_NAME LIKE 'mv-%';
```

### Inspect Task Manager

```sql
-- Global pending/running task status
SELECT inspect_task_runs();
```

---

## Task Manager UI

### Task List

```
┌─ Tasks ─────────────────────────────────────────────────┐
│                                                          │
│  [+ Create Task]                                         │
│                                                          │
│  Name         Status   Schedule    Last Run   Actions    │
│  etl_step1    ACTIVE   Manual      2m ago ✅  [⏸][🗑]  │
│  etl_hourly   ACTIVE   Every 1h    15m ago ✅ [⏸][🗑]  │
│  etl_daily    PAUSE    Every 1d    —          [▶][🗑]   │
│  mv_refresh   ACTIVE   Auto        5m ago ✅  [⏸][🗑]  │
└──────────────────────────────────────────────────────────┘
```

### Task Run History

```
┌─ Task Runs: etl_hourly ─────────────────────────────────┐
│                                                          │
│  Run ID    Created       Finished      Status   Duration │
│  #142      18 Jun 14:00  18 Jun 14:02  ✅       2m      │
│  #141      18 Jun 13:00  18 Jun 13:01  ✅       1m      │
│  #140      18 Jun 12:00  18 Jun 12:00  ❌       0s      │
│          Error: key size exceeded                         │
│  #139      18 Jun 11:00  18 Jun 11:02  ✅       2m      │
└──────────────────────────────────────────────────────────┘
```

### Task Concurrency

Verified against a live StarRocks 4.1.1 engine (NOVA-23, 2026-09-17). These are
real `SHOW VARIABLES` entries — the earlier `task_runs_ttl_second = 86400` figure
in this doc was **wrong**; the engine default is **604800 (7 days)**.

| Config | Default | Description |
|--------|---------|-------------|
| `task_runs_concurrency` | 4 | Max parallel TaskRuns |
| `task_runs_queue_length` | 500 | Max pending TaskRuns |
| `task_ttl_second` | 86400 | Task TTL (one-shot) |
| `task_runs_ttl_second` | **604800** | TaskRun TTL — **7 days**, not 24 h |
| `task_runs_max_history_number` | 10000 | Max TaskRun history retained |
| `task_runs_timeout_second` | 14400 | TaskRun execute timeout (4 h) |
| `task_min_schedule_interval_s` | 10 | Minimum schedule interval |
| `task_check_interval_second` | 60 | Interval of task background scheduled jobs |
| `max_task_consecutive_fail_count` | 10 | Consecutive failures before the task **auto-pauses** |

`task_check_interval_second = 60` is the scheduling granularity of the native
scheduler: a `SCHEDULE EVERY(INTERVAL 10 SECOND)` task is accepted (10 s is the
minimum), but the FE only scans for due tasks once per minute, so sub-minute
cadence is not honoured in practice. This is a strong argument for Nova owning
its own tick (see NOVA-23).

---

## Limitations

- **No task dependencies/DAG** — Each task is independent
- **No callback/trigger** on completion
- **No conditional branching** (if A fails, run C)
- For orchestration, use external tools (Airflow, n8n) that poll `information_schema.task_runs`

### Verified against a live 4.1.1 engine (NOVA-23, 2026-09-17)

All five limitations above were probed directly against
`starrocks/fe-ubuntu:4.1.1` and confirmed. The rejection messages are the parser's
own, so these are grammar-level absences, not engine settings awaiting a flag:

| Requested clause | Engine answer |
|---|---|
| `AFTER <task>` (dependency) | `Unexpected input 'AFTER', the most similar input is {'PROPERTIES', 'AS', 'SCHEDULE'}` |
| `WHEN (…)` (stream/condition) | `Unexpected input 'WHEN', the most similar input is {'SCHEDULE', 'PROPERTIES', 'AS'}` |
| `FINALIZE <stmt>` | `Unexpected input 'INSERT', the most similar input is {<EOF>, ';'}` |
| `ALLOW_OVERLAPPING_EXECUTION = TRUE` | `Unexpected input 'ALLOW_OVERLAPPING_EXECUTION', the most similar input is {'PROPERTIES', 'AS', 'SCHEDULE'}` |
| `SCHEDULE = 'USING CRON …'` | `Unexpected input '='` |

**The task body is restricted by the grammar itself, not by runtime validation.**
`SUBMIT TASK` accepts only CTAS / `INSERT` / `CACHE SELECT` (plus `DESC`/`DESCRIBE`/
`EXPLAIN`/`CREATE` per the parser's suggestions below). Each of the following was
rejected at parse time: `CREATE TABLE`, `DROP TABLE`, `UPDATE`, `CREATE VIEW`,
`SET @x = 1`, and a bare `SELECT 1`. A Nova worker therefore **cannot** be a thin
wrapper around `SUBMIT TASK` for arbitrary SQL — for anything outside those three
statements, Nova must execute the statement itself.

Two additional constraints that matter for a Nova scheduler:

- **No run-history hook and no dependency signal** exist, so every
  "task finished → run the next one" transition must **poll**
  `information_schema.task_runs`. There is no alternative.
- **Tasks auto-pause after `max_task_consecutive_fail_count` (10) consecutive
  failures.** A Nova DAG that a paused task sits in will silently stop advancing
  unless Nova reconciles this state.

### Provider blocker: `information_schema.partitions` is empty

The planned `partition_change` stream provider assumed
`information_schema.partitions` could supply a watermark. On the live 4.1.1 engine
**the table returns 0 rows for every schema**, including Nova's own partitioned
tables, and stays empty after `ANALYZE TABLE`. `SHOW PARTITIONS` *does* return
data, but its `UPDATE_TIME` column **did not move across three inserts** into the
partition (it only changes on partition DDL), and `TABLE_ROWS` stayed `0`
throughout. `information_schema.partitions` therefore has no `DATA_VERSION` column
either — that column name does not exist.

Conclusion: `partition_change` is **not implementable as designed** and needs a
replacement mechanism or removal; do not schedule it without a fresh design.
