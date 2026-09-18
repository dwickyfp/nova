# Module 08: Task Manager

> Manage asynchronous ETL tasks: SUBMIT TASK, ALTER TASK, task runs, scheduling.

---

## Task Concepts

| Concept | Description |
|---------|-------------|
| **Task** | Template for an async ETL job (INSERT, CTAS) |
| **TaskRun** | Single execution instance of a task |
| **Schedule** | One-shot or periodic (SCHEDULE EVERY) |

> **Statement surface.** The only task statement StarRocks 4.1.1 has is
> **`SUBMIT TASK`**. `CREATE TASK` is **not** an engine statement (`No viable
> statement for input 'CREATE TASK'`), and neither is `DROP TASK` or `SHOW TASKS`
> — see `docs/GUIDE_OBJECTS.md`. Everything sent to the engine uses `SUBMIT TASK`.
>
> Nova now also parses the **Nova surface** `CREATE TASK … AFTER / FINALIZE / WHEN
> / SCHEDULE / OVERLAP_POLICY` and lowers it to `CONFIG_TASK*` metadata (Phase 9,
> stage 1 grammar + stage 2 lowering, NOVA-54). That statement is **Nova-only**:
> it is intercepted in the query pipeline and **never reaches the engine**. See
> "Nova `CREATE TASK` surface" below. The design lives in
> `docs/specs/nova-23-task-orchestration-design.md`.

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

### Nova `CREATE TASK` surface (NOVA-54, Phase 9)

Nova parses a Snowflake-shaped `CREATE TASK` and lowers it to `CONFIG_TASK*`
metadata. The statement is intercepted in the query pipeline and **never sent to
the engine**; each node's `SUBMIT TASK` is built by the worker from the stored
body at execution time.

```sql
-- Cron root task (Nova cron, not an engine cron)
CREATE TASK etl_root
  SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta'
  AS INSERT OVERWRITE agg_daily SELECT * FROM staging;

-- Dependency edge
CREATE TASK etl_clean
  AFTER etl_root
  AS INSERT OVERWRITE agg_clean SELECT * FROM agg_daily;

-- Multiple parents and a condition
CREATE TASK etl_join
  AFTER etl_a, etl_b
  WHEN load_count > 0
  OVERLAP_POLICY = 'QUEUE'
  AS INSERT INTO etl_log SELECT 1;

-- Finalizer
CREATE TASK etl_notify
  FINALIZE etl_root
  AS INSERT INTO etl_log SELECT 1;
```

Clause rules enforced by the lowering (the grammar accepts more than the surface
allows):

| Rule | Detail |
|---|---|
| **Order** | `AFTER`, `FINALIZE`, `WHEN`, `OVERLAP_POLICY`, `SCHEDULE` — any other order is rejected. |
| **No duplicates** | Each clause appears at most once. |
| **`SCHEDULE`** | `SCHEDULE = '<cron>'` maps to `schedule_kind="cron"`; an optional `USING CRON` prefix and an optional trailing IANA zone are accepted (`'0 2 * * * Asia/Jakarta'`). `SCHEDULE START(…) EVERY(…)` maps to `interval`. An invalid cron is rejected before anything is stored. |
| **`OVERLAP_POLICY`** | One of `skip` / `queue` / `allow` (case-insensitive). Anything else is rejected. Defaults to `skip`. |
| **`FINALIZE` / `OVERLAP_POLICY` spelling** | `FINALIZE b` and `FINALIZE = b` are the same clause. |
| **`WHEN`** | Stored verbatim, so `AND`/`OR` structure is preserved. |
| **Body** | `CTAS | INSERT | CACHE SELECT` only; `AS SELECT` is rejected by the grammar. |
| **Cycles** | A statement that would close a cycle in the merged graph is rejected and rolled back. |
| **`AFTER`** | One task cannot depend on itself; a repeated parent is rejected. |

`OVERLAP_POLICY` values are validated against the Nova enum
(`backend/app/modules/task_orchestration/schemas.py`); `FINALIZE` edges are stored
with `edge_kind='finalize'` and excluded from the dependency adjacency until the
scheduler/worker wiring lands (PR 3b).

An `@stage` reference in the body is **not** usable yet: the pinned StarRocks
grammar has no `@stage` rule, so the statement fails at parse time. Worker-side
stage translation is blocked on the NOVA-17 grammar work.

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
row.

> **Fixed (NOVA-34 / D9.8).** Nova used to leak all tasks: `TaskService._connect()`
> opened a **root** connection with `STARROCKS_ROOT_USER` and the router passed only
> `get_current_user` for authentication, so the caller was never threaded into the
> query. `TaskService` now takes an injected `asyncmy` connection as the first
> argument of every method, and the router supplies it through
> `get_user_connection` (`backend/app/core/deps.py`) — the pattern `users` and
> `object` browsing already use. `GET /tasks` and the rest of the task surface now
> run as the calling user, and the engine's privilege filter applies. There is no
> root connection left in `backend/app/modules/tasks/`.
>
> `get_user_connection` itself had a latent bug — it did `await db.user_conn(...)`
> on an `@asynccontextmanager` factory, which raises `TypeError` at request time.
> It is corrected to `async with db.user_conn(...) as conn:`.

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
**FE configs**, read via `ADMIN SHOW FRONTEND CONFIG LIKE '%task%'` — **not**
session variables. A plain `SHOW VARIABLES LIKE 'task_…'` returns nothing for them
(and a broad `SHOW VARIABLES LIKE '%task%'` only ever includes them intermittently,
which is a trap: do not validate them that way). The earlier
`task_runs_ttl_second = 86400` figure in this doc was **wrong**; the engine default
is **604800 (7 days)**.

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
| `enable_task_history_archive` | true | Task run history archiving |

**TaskRun history cannot be deleted.** There is no `CLEAR TASK RUNS` statement
(rejected at parse) and `DELETE FROM information_schema.task_runs` fails with
`Where clause is not set`. Dropping a task does **not** delete its run rows — they
persist until TTL/archive. Any Nova UI that drops a task must not assume the run
history went with it.

`task_check_interval_second = 60` is the scheduling granularity of the native
scheduler: a `SCHEDULE EVERY(INTERVAL 10 SECOND)` task is accepted (10 s is the
minimum), but the FE only scans for due tasks once per minute, so sub-minute
cadence is not honoured in practice. This is a strong argument for Nova owning
its own tick (see NOVA-23).

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
| `CONFIG_TASK_EDGES` | directed `parent_task → child_task` per `graph_id` | one row per edge (`edge_kind` is `after` or `finalize`; supports multi-parent, cycle detection, delete-impact queries) |
| `CONFIG_TASK_GRAPH_RUNS` | one row per graph execution | `trigger_type`, `state`, `overlap_policy` (copied from the root task at enqueue), `wal_marks` (JSON metadata), `started_at`, `finished_at` |
| `CONFIG_TASK_RUNS` | one row per node attempt | `graph_run_id`, `task_id`, `attempt`, `state`, `delegated`, `starrocks_query_id`, `error_message` |

### Runtime semantics (stage 3b)

These are the behaviours the `CREATE TASK` surface promises, and where each is
enforced.

**`AFTER` — dependency order.** The graph's adjacency is built from
`edge_kind='after'` edges; a node is ready when every parent has succeeded (or
been suspended, which never holds a join open). A failed parent fails the graph
and skips its descendants.

**`FINALIZE` — after the graph, never alongside it.**

> **This is a deliberate decision, not an accident of implementation.** It is
> written down here so a reader does not mistake the failure behaviour for a bug.
> The alternatives considered and rejected are listed below.

A finalizer is **not** a dependency. Two consequences drive the implementation:

1. **It must never be offered as a zero-dependency root.** A node with no
   incoming edge satisfies `all(parents succeeded)` vacuously, so if a finalizer
   were left in the dependency graph it would be `ready` on the first transition
   and run alongside — or before — the work it follows. Finalizer nodes are
   therefore **removed from the dependency graph entirely** and staged
   separately.
2. **It runs only after the graph completes, never alongside it.** The rule:
   a finalizer is enqueued only once the **entire dependency graph** has settled
   successfully (every dependency node `success` or `suspended`). The
   whole-graph reading is chosen over "after the specific target's ancestors"
   because the latter leaves a concurrency window with a downstream node.

**Decision: a failed or skipped dependency graph skips the finalizer.** A
finalizer is an engine task submitted as the owner, not a callback. Running it
over a failed run would execute a write (e.g. `INSERT INTO etl_log`) that claims
something which did not happen — a misreport, not a cleanup. It is also
unsafe-by-default: a finalizer would have to be failure-tolerant by
construction, and that is the task author's call, not the engine's. So the safe
default is not to run it.

**A finalizer's own failure fails the graph** (recorded and audited; the
dependent tasks are not silently marked successful). A finalizer's own `WHEN` is
honoured like any other node.

*Future option, not implemented:* an explicit per-task "run the finalizer even on
failure" flag would be the way to add always-run semantics, so the decision stays
with the author. No such flag exists today.

**`WHEN` — conditional skip.** Evaluated on the owner's connection before the
node runs. False marks the node `skipped`, and its descendants are skipped too.
An evaluation **error** fails the node; an error is never treated as "no data".

**`OVERLAP_POLICY` — enforced at graph-run enqueue and claim.** Stage 2 stored and
validated the value; the scheduler and worker now act on it:

| Policy | Behaviour |
|---|---|
| `skip` | If any run for the graph is still `pending`/`running`, the due occurrence is **not** enqueued (it is dropped). |
| `queue` | The run **is** enqueued; the worker **defers** it while another run for the same graph is active, then runs it. |
| `allow` | The run **is** enqueued and may execute **concurrently** with an active run. |

An unknown or absent value behaves as `skip` — the strictest policy — so a bad
value can never start an overlap by accident.

**`SCHEDULE` — cron/interval firing.** Only root tasks (no incoming edge) are
schedule anchors; one due root creates one graph run covering every reachable
node. A cron expression is validated at create time by `schedule.parse_cron` and
evaluated by the scheduler tick against the task's own IANA timezone.

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
├── graph.py       # pure DAG validation (acyclic, node/parent/child limits)
├── ddl.py         # pure CREATE TASK -> LoweredTask (parse + validate)
├── lowering.py    # persist a LoweredTask as CONFIG_TASK* rows (cycle-checked)
├── dag.py         # pure graph-run state machine + finalizer staging
├── scheduler.py   # due-graph planning + overlap_policy enforcement at enqueue
└── worker.py      # executes ready nodes, finalizers, WHEN, delegate-first
```

---

## Limitations

- **Native engine has no task dependencies/DAG** — Nova's Phase 9 layer adds them; until the scheduler/worker stages land (9a onward), Nova-shaped DAGs are metadata only and not executed
- **No callback/trigger** on completion — progress must be observed by polling `information_schema.task_runs`
- **No conditional branching** (if A fails, run C)
- For orchestration today, use external tools (Airflow, n8n) that poll `information_schema.task_runs`

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

### Provider blocker: `information_schema.partitions` is empty (superseded)

The planned `partition_change` stream provider first assumed
`information_schema.partitions` could supply a watermark. On the live 4.1.1 engine
**the table returns 0 rows for every schema**, including Nova's own partitioned
tables, and stays empty after `ANALYZE TABLE`. It also has no `DATA_VERSION`
column — that column name does not exist.

**That conclusion was wrong, and the provider is live.** The error was querying an
unpopulated MySQL-compatibility view instead of the real surface. `SHOW PARTITIONS`
is the live surface, and its **`VisibleVersion`** column advances per-partition on
every non-DDL load while untouched partitions stay put (verified: an insert into
`p1` moved only `p1`; an insert into `p2` moved only `p2`). `partition_change`
therefore has a real, monotonic, per-partition watermark.

Two operational notes for the provider:

- Use **one full `SHOW PARTITIONS` sweep, then diff in Python**. The `WHERE` filter
  is accepted but not cheaper than a full scan (measured at 5,000 partitions:
  full sweep ~110–260 ms; per-partition `WHERE` ~65–150 ms for one row), so
  per-partition lookups are ~300× slower in aggregate. `IN (...)` is rejected.
- `SHOW PARTITIONS.UPDATE_TIME` is **DDL-only** and remains unusable as a
  watermark; `VisibleVersion` is the column to use.

Full rationale and measurements: `docs/specs/nova-23-task-orchestration-design.md`
§6 / D9.6.
