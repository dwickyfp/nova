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

## Limitations

- **No task dependencies/DAG** — Each task is independent
- **No callback/trigger** on completion
- **No conditional branching** (if A fails, run C)
- For orchestration, use external tools (Airflow, n8n) that poll `information_schema.task_runs`
