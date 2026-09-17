# Phase 9 — Task Orchestration & Scheduler (NOVA-23 design)

> Design decisions for cron, DAG, SQL-defined tasks, stream triggers, and a
> scheduler/worker engine separate from the FastAPI backend.
> Status: **decisions taken, pending human approval on three product items.**
> Every constraint marked *verified* was probed against the live
> `starrocks/fe-ubuntu:4.1.1` instance on 2026-09-17, not read from docs.

---

## 0. What was decided, and against which criteria

The user asked for planning and design decisions weighed on five axes:
**useful, resource, performance, clean code, clean architecture.** Each decision
below names the axis it was optimised for and the axis it costs.

| # | Decision | Optimised for | Cost accepted |
|---|---|---|---|
| **D9.1** | Own the cron/DAG engine; do **not** adopt a workflow framework | clean architecture, resource | Nova maintains execution orchestration (~600–900 LOC) |
| **D9.2** | Two processes: `nova-scheduler` (singleton) + `nova-worker` (N); Redis Streams transport; `NOVA_SYSTEM` is the only source of truth | clean architecture, performance | Two more processes to run and monitor |
| **D9.3** | `CREATE TASK … AFTER / FINALIZE / WHEN / OVERLAP_POLICY` — a Snowflake superset that lowers to `SUBMIT TASK` + Nova metadata. No `CREATE DAG … STEP` | useful, clean code | Nova maintains a patch over the StarRocks grammar |
| **D9.4** | **Delegate-first**: run `SUBMIT TASK` on the *submitter's own connection*, so StarRocks RBAC is the enforcement. Nova executes directly only what the engine cannot | clean architecture, useful | MVP cannot run arbitrary DDL as a task (see E2) |
| **D9.5** | Phase 1 = cron + DAG + SQL-defined + metadata + engine. **Stream is Phase 2**, and ships with **one** provider (`mv_refresh`) | useful, resource | The user's "has stream" request lands later than the rest |
| **D9.6** | `partition_change` is **dropped**, not deferred | clean code, resource | No partition-level trigger until a real watermark source exists |
| **D9.7** | Run history snapshot into `NOVA_SYSTEM` — but as **DAG state**, not as a 24 h-loss mitigation | clean architecture | Some duplication of native `task_runs` |
| **D9.8** | Fix the `GET /tasks` root-connection RBAC defect **inside** Phase 9 | clean architecture | Slightly larger phase scope |

---

## 1. Engine facts that drove the design (all verified)

These were measured on the running engine. Several contradict the original
research premises, so they are stated first.

| Fact | Consequence for the design |
|---|---|
| **No cron.** `SCHEDULE = 'USING CRON …'` is rejected at parse time (`Unexpected input '='`). Only `MANUAL`, `SCHEDULE EVERY(INTERVAL …)`, `SCHEDULE START('<literal>') EVERY(…)` are accepted | A cron parser and next-fire calculator are **Nova's**, not a translation layer |
| **`START` literals use the session timezone** (`Asia/Jakarta`), not UTC | The scheduler must store an explicit IANA timezone per task and never assume UTC |
| **`task_runs_ttl_second = 604800` (7 days)**, not 86400 | The "native history is lost in 24 h" premise was wrong; snapshotting is still useful, but for **graph state and lineage**, not data-loss |
| **No `AFTER` / `WHEN` / `FINALIZE` / `ALLOW_OVERLAPPING_EXECUTION`** — all rejected by the grammar | DAG edges, conditionals, finalizers and overlap policy are Nova metadata |
| **No completion hook; no nested `SUBMIT TASK`** (`Unexpected input 'SUBMIT'`) | Progress between nodes can **only** be observed by polling `information_schema.task_runs` |
| **TaskRun privileges are checked at `SUBMIT TASK`, against the submitter**, and recorded in `CREATOR`; the engine also **filters reads by caller privilege** | If Nova submits on the user's connection, RBAC enforcement is free (D9.4) |
| **The engine enforces the body restriction in the grammar**: only CTAS / INSERT / CACHE SELECT parse | "Body must be delegatable" is validated by the parser, not by Nova |
| **`information_schema.partitions` returns 0 rows for every schema** and has no `DATA_VERSION`; `SHOW PARTITIONS.UPDATE_TIME` moves on DDL only | `partition_change` has no data source — dropped (D9.6) |
| **Periodic tasks survive an FE restart and reschedule automatically** | Nova's reconciliation burden is much smaller than feared: it does not need to re-submit or re-resume schedules |
| **`information_schema.tasks` has no `STATE` column**; `SHOW TASKS` is not a statement | Suspend/resume state is not queryable; Nova derives it from the `SCHEDULE` string and its own records |
| **A task auto-pauses after `max_task_consecutive_fail_count = 10`** | A DAG can silently stop advancing; the reconciler must detect and surface this |
| **`task_check_interval_second = 60`** — the FE scans for due tasks once a minute | Native sub-minute cadence is not honoured; Nova's own tick is required for anything finer |
| **TaskRun rows cannot be deleted** — no `CLEAR TASK RUNS`, `DELETE` requires a where clause, and `DROP TASK` leaves the runs | Run history is append-only by construction; a Nova UI must not imply deletion |

---

## 2. D9.1 / D9.2 — Engine: why Nova owns orchestration

### The option that was rejected, and why

The research matrix (proposal §4) was correct to reject Prefect and Temporal:
both introduce a second control plane and a second database, which violates the
"Single Database" invariant in `AGENTS.md`. Celery/RQ/Dramatiq were rejected for
a subtler and more durable reason: **they do not provide a DAG**, so adopting one
would still leave Nova writing the graph engine, retry, skip-propagation and
finalizer semantics — while adding a broker, a result backend that drifts toward
a second source of truth, and an extra failure domain.

The engine facts make this decisive: because there is **no completion hook**, every
"node finished → run the next" transition must poll `information_schema.task_runs`.
That polling loop is unavoidable Nova code. A workflow framework cannot remove it —
it would only wrap it. So the framework adds surface without removing the work.

### The architecture

```
                    ┌──────────────────────┐
   cron / manual ──▶│   nova-scheduler     │  singleton, leader-lock in Redis
   (no HTTP path)   │  - next-fire calc    │  - never executes SQL
                    │  - writes graph_run  │  - writes NOVA_SYSTEM first
                    └──────────┬───────────┘
                               │ XADD graph_run
                        ┌──────▼──────┐
                        │Redis Streams│  transport only (ephemeral)
                        └──────┬──────┘
                               │ XREADGROUP
                    ┌──────────▼───────────┐
                    │   nova-worker (N)    │  - executes nodes
                    │  - delegate SUBMIT   │  - polls task_runs
                    │    TASK on user conn │  - advances graph state
                    └──────────┬───────────┘
                               │ writes
                    ┌──────────▼───────────┐
                    │  NOVA_SYSTEM (SR)    │  source of truth, durable
                    │  CONFIG_TASK*        │
                    └──────────────────────┘
```

**Clean-architecture rules that make this safe:**

1. **`NOVA_SYSTEM` is written before Redis.** A graph run is persisted, *then*
   the job is pushed. If Redis is flushed, no work is lost — the reconciler
   re-derives it. Redis is never a source of truth.
2. **The scheduler never executes SQL.** It only computes time and creates graph
   runs. Separating "decide" from "do" is what lets the scheduler be a singleton
   while workers scale horizontally.
3. **The worker is stateless across restarts.** All state it needs is in
   `NOVA_SYSTEM`; an in-flight `RUNNING` row without a heartbeat is treated as
   abandoned and re-evaluated, not trusted.
4. **At-least-once delivery is assumed.** Handlers are idempotent by design, which
   the DAG state machine makes explicit (a node transition is a conditional write
   on current state, never a blind increment).

**Resource/performance note:** the scheduler is one small process doing a time
comparison per task per tick. The workers are the only component that talks to
StarRocks for execution. There is no second database, no broker beyond the Redis
that already runs, and no polling storm — the reconciler polls only graph runs
that are actually `RUNNING`, not every task.

**Reopen triggers:** throughput beyond ~50 runs/s, graphs beyond ~1000 nodes,
multi-region or per-tenant SLA, or StarRocks shipping a completion hook (which
would let the polling loop be deleted).

---

## 3. D9.3 — SQL surface, and the grammar cost

**Chosen: the Snowflake superset.**

```sql
CREATE TASK etl_root
  SCHEDULE = 'USING CRON 0 2 * * * Asia/Jakarta'
  AS INSERT OVERWRITE agg_daily SELECT * FROM staging;

CREATE TASK etl_clean
  AFTER etl_root
  AS INSERT OVERWRITE agg_clean SELECT * FROM agg_daily;

CREATE TASK etl_notify
  FINALIZE = etl_root
  AS INSERT INTO etl_log VALUES (NOW(), 'done');
```

`CREATE DAG … STEP …` was rejected: it forces Nova to maintain a grammar that is
*not* a superset of StarRocks forever, and it makes every future StarRocks grammar
re-sync a merge conflict by construction.

**Why the grammar cost is small** (verified against upstream, addendum §A): three
of the five new clauses need **no new lexer token** — `AFTER`, `WHEN` and
`SCHEDULE` already exist, and `AFTER`/`SCHEDULE` are already in `nonReserved`, so
they can be used as identifiers without conflict. `taskClause` is already
`taskClause*`, so adding alternatives does not touch `submitTaskStatement`. Only
`FINALIZE`, `CRON` and `OVERLAP_POLICY` need new tokens, and all three **must be
added to `nonReserved`** — otherwise columns named `finalize`/`cron` break. That
is a concrete breaking-change risk, not a theoretical one.

The Nova extension is kept as a **separate patch applied with `--fuzz=0`**, over a
byte-identical upstream copy, with `NOVA-BEGIN`/`NOVA-END` markers and a CI drift
check. A fuzzy apply that lands a rule in the wrong place must be a hard build
failure.

**Clean-code consequence:** `CREATE TASK` is *parsed* by Nova and *lowered* to
`SUBMIT TASK` plus metadata rows — the same parse → translate → execute pipeline
already used for `@stage` → `FILES()`. No new architectural pattern is introduced.

---

## 4. D9.4 — Authorization: delegate first

The engine checks privileges **at submit time against the submitter** and records
them in `CREATOR` (verified). This makes the correct design the cheap one:

- The worker submits `SUBMIT TASK` using the **owning user's connection**, not a
  root connection. StarRocks then enforces RBAC for the run, for free.
- For statements the engine's grammar cannot accept, the worker runs them on the
  same user connection.
- Nova stores `owner_role` and `created_by` as **metadata** — never a password,
  never a token. The `Credential-Invisible` invariant is untouched.

**The pre-existing defect this depends on fixing.** `TaskService._connect()`
(`backend/app/modules/tasks/service.py:32-38`) opens a **root** connection, and the
router passes only `get_current_user` for authentication
(`backend/app/modules/tasks/router.py:38,51`) — the caller is never threaded into
the query. The result is that `GET /tasks` returns every task to every signed-in
user, even though the engine would have filtered them. Verified: a user with no
grants sees an empty `information_schema.tasks`; the endpoint does not.

This is not a StarRocks limitation — it is a bug, and it sits directly in the path
Phase 9 builds on. It is fixed here (D9.8) using the pattern `users` and
`resource_groups` already use: `get_user_connection`
(`backend/app/core/deps.py:75`).

---

## 5. D9.7 — Metadata, and what is deliberately *not* stored

Four tables, following the existing flat `CONFIG_*` convention:

| Table | Holds |
|---|---|
| `CONFIG_TASKS` | definition, schedule kind + expr + **IANA timezone**, `when_expr`, `overlap_policy`, `owner_role`, `created_by`, version |
| `CONFIG_TASK_EDGES` | `parent_task` → `child_task` per graph |
| `CONFIG_TASK_GRAPH_RUNS` | one row per graph run: trigger type, state, `wal_marks` (JSON), timings |
| `CONFIG_TASK_RUNS` | one row per node: attempt, state, `delegated`, `starrocks_query_id`, timings |

**Edges in their own table** rather than a CSV column: it supports multiple
parents, makes cycle detection a normal query instead of string surgery, and makes
"what breaks if I delete this task" answerable. DAG validation (acyclic, ≤1000
nodes, ≤100 parents/children) is backend logic, matching the Snowflake limits.

**No credentials, ever.** `wal_marks` holds partition names, IDs and timestamps —
metadata only. The one rule that must not bend: if Nova needs to reference a
credential, it stores the *name* of an object (e.g. a stage's storage connection),
never its value.

**Run history is snapshotted into `NOVA_SYSTEM` — for the right reason.** The
original justification was data loss after 24 h; that premise is false (TTL is
7 days). The real reasons are: graph state must survive so a DAG can resume, native
`task_runs` mixes MV and Nova tasks, and native rows **cannot be deleted** so they
are not a usable working table. This correction matters — it means snapshotting is
a design choice for correctness, not a race against a TTL.

---

## 6. D9.5 / D9.6 — Stream triggers, and the provider that died

**Principle (from Snowflake, accepted):** a signal may false-positive, but must not
false-negative. Consequences that are non-negotiable:

- Idempotency lives in the **task body**, not the signal. A task with a stream
  provider whose body is a blind `INSERT INTO` a non-PK table is **rejected at
  create time** — fast feedback, and the task never reaches an unsafe state.
- `WHEN` evaluation must be cheap. Nova must **not** compute "how many rows
  changed" — that means `COUNT(*)` on the user's table on every evaluation.
- The watermark advances **only after a run succeeds**, never when `WHEN` is true.
- An **error** during `WHEN` evaluation is not "no data" — it must be recorded as a
  run that did not execute, never skipped silently.
- `AND`/`OR` structure is preserved; the executor must not flatten it to "any".

**`partition_change` is dropped, not deferred.** It was the most fragile provider
by design, and the engine ruled it out empirically: `information_schema.partitions`
returns **zero rows for every schema** (including Nova's own tables) and stays empty
after `ANALYZE`, there is no `DATA_VERSION` column, and `SHOW PARTITIONS.UPDATE_TIME`
does **not** move across inserts — it only changes on partition DDL. The provider has
no usable data source. Dropping it is a deletion of dead design, not a deferral.

**Phase 2 ships one provider: `mv_refresh`.** It is the only native
"data changed → work runs" primitive in StarRocks, and its `SKIPPED` state is
meaningful. `load_event` (keyed on the globally unique `ID` from
`information_schema.loads`) is the natural second, and is not blocked by anything —
it is simply later. Task MV runs are identified by matching against
`information_schema.materialized_views`, **not** `LIKE 'mv-%'` — the naming
heuristic is fragile and mixes Nova tasks with MV tasks in the same view.

---

## 7. Scope, sequencing, and what is still a human decision

### Proposed Phase 9 staging

| Stage | Content | Depends on |
|---|---|---|
| **9a** | Metadata tables, DAG validation, `nova-scheduler` tick + cron, `nova-worker`, delegate-first execution, reconciliation | — |
| **9b** | `CREATE TASK` grammar patch + lowering; task graph UI | 9a |
| **9c** | Stream provider `mv_refresh`; `load_event` | 9a |

9a is the vertical slice that makes the rest real. 9b is the user-visible surface.
9c is the feature the user asked for that the engine does not provide.

### Still needs a human decision

These shape the user-visible surface or the authorization model, so they are
raised rather than decided silently:

1. **E1 — confirm the SQL surface.** D9.3 recommends `CREATE TASK … AFTER …` over
   `CREATE DAG … STEP …`. This is the user's SQL, so it needs a yes.
2. **E2 — non-delegatable tasks.** With delegate-first (D9.4), Phase 1 supports
   task bodies the engine accepts. Tasks that need arbitrary DDL require a
   dedicated StarRocks service role — a change to the RBAC model, which is a
   human decision. Recommendation: **ship Phase 9 without it** and revisit only if
   a real need appears.
3. **E3 — the stream deferral.** D9.5 puts stream in Phase 2. If the user wants
   `has stream` in Phase 1, that is a scope call, and `mv_refresh`-only is the
   only honest way to do it.
4. **`SYSTEM$STREAM_HAS_DATA` naming** — keep the Snowflake-compatible name, or use
   a Nova name. Tied to E1; recommendation is to keep it, since parity is the goal.

### Reopen triggers

- StarRocks gains cron, `AFTER`, or a completion hook → delete the corresponding
  Nova component rather than maintain it.
- `information_schema.partitions` gains a real per-partition version → revisit a
  partition provider.
- Redis is removed from the stack, or a managed orchestrator is approved as a
  dependency → the D9.2 decision reopens.

---

## 8. Definition of done for the phase

- Scheduler and worker run as separate processes, documented in `HOW_TO_RUN.md`.
- A cron task and an `A→B→[C,D]` DAG run to completion, with sibling parallelism
  and a join that waits for all parents.
- `SUBMIT TASK` is submitted on the **user's** connection; a restricted user's task
  touching a forbidden table fails, verified by test.
- `GET /tasks` respects the caller's grants; verified against a low-privilege user.
- No credential appears in any `CONFIG_TASK*` row or API response.
- Run history survives a worker restart and a Redis flush.
- Every state transition writes `NOVA_SYSTEM.AUDIT_LOG`.
- `ruff` + `mypy` + tests pass; grammar drift check passes.
