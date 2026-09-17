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

| State | Description |
|-------|-------------|
| `ACTIVE` | Task is registered and ready |
| `PAUSE` | Task is suspended |

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

| Config | Default | Description |
|--------|---------|-------------|
| `task_runs_concurrency` | 4 | Max parallel TaskRuns |
| `task_runs_queue_length` | 500 | Max pending TaskRuns |
| `task_ttl_second` | 86400 | Task TTL (one-shot) |
| `task_runs_ttl_second` | 86400 | TaskRun TTL |
| `task_min_schedule_interval_s` | 10 | Minimum schedule interval |

---

## Nova Orchestration Metadata (Phase 9)

The native engine above has **no DAG, no `AFTER`/`WHEN`/`FINALIZE`, no cron, and no
completion hook**. Nova adds those as its own orchestration layer, separate from the
native task wrapper in `backend/app/modules/tasks/` (which only administers
`information_schema.tasks`). Phase 9a ships the **state layer only** — no scheduler,
worker, or execution.

### Tables

All four live in `NOVA_SYSTEM` as Primary-Key (CRUD) tables, following the flat
`CONFIG_*` convention in `docker/init-nova.sql`.

| Table | Holds | Key columns |
|-------|-------|-------------|
| `CONFIG_TASKS` | one row per task definition | `name`, `definition`, `schedule_kind` (`manual`/`interval`/`cron`), `schedule_expr`, `timezone` (IANA), `when_expr`, `overlap_policy`, `owner_role`, `created_by`, `version` |
| `CONFIG_TASK_EDGES` | directed `parent_task → child_task` per `graph_id` | one row per edge (supports multi-parent, cycle detection, delete-impact queries) |
| `CONFIG_TASK_GRAPH_RUNS` | one row per graph execution | `trigger_type`, `state`, `wal_marks` (JSON metadata), `started_at`, `finished_at` |
| `CONFIG_TASK_RUNS` | one row per node attempt | `graph_run_id`, `task_id`, `attempt`, `state`, `delegated`, `starrocks_query_id`, `error_message` |

### Design rules

- **Credential-invisible (hard invariant).** No column may be named
  `password`/`secret`/`token`/`credential`, and `wal_marks` holds only metadata
  (partition names, IDs, timestamps). To reference a credential, store the *name*
  of the object (e.g. a storage-connection name), never its value. A test reads
  `information_schema.columns` for `CONFIG_TASK%` and asserts this holds.
- **Timezones are explicit IANA.** The engine's `SCHEDULE START` literals use the
  session timezone, so Nova never assumes UTC — every task carries its own `timezone`.
- **Edges are rows, not a CSV.** Multi-parent graphs and cycle checks become plain
  queries instead of string surgery. Edges store task **names**
  (`parent_task`/`child_task`) — not ids — so graph membership is resolved by name,
  and a task belongs to a graph when it is either endpoint of an edge.
- **DAG limits** (validated in pure code, no I/O): acyclic, ≤ 1000 nodes, ≤ 100
  parents and ≤ 100 children per task.
- **Writes are column-whitelisted.** `update_*` validates payload keys against a
  per-entity whitelist before building the `SET` clause, so an unknown or
  injection-shaped key is rejected rather than spliced into SQL. Empty payloads are
  rejected too.

### Module layout

```
backend/app/modules/task_orchestration/
├── schemas.py     # Pydantic models for Task / Edge / GraphRun / TaskRun
├── repository.py  # CRUD via db.execute_system (no ad-hoc root connections)
└── graph.py       # pure DAG validation (acyclic, node/parent/child limits)
```

---

## Limitations

- **Native engine has no task dependencies/DAG** — Nova's Phase 9 layer adds them; until the scheduler/worker stages land (9a onward), Nova-shaped DAGs are metadata only and not executed
- **No callback/trigger** on completion — progress must be observed by polling `information_schema.task_runs`
- **No conditional branching** (if A fails, run C)
- For orchestration today, use external tools (Airflow, n8n) that poll `information_schema.task_runs`
