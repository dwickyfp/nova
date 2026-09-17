# Phase 9 — Task Orchestration & Scheduler (NOVA-23 design)

> Design decisions for cron, DAG, SQL-defined tasks, stream triggers, and a
> scheduler/worker engine separate from the FastAPI backend.
> Status: **decisions taken; E1–E3 confirmed 2026-09-17; Phase 9a in progress.**
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
| **D9.3** | `CREATE TASK … AFTER / FINALIZE / WHEN / OVERLAP_POLICY` — a Snowflake superset that lowers to `SUBMIT TASK` + Nova metadata. This is a **new Nova grammar surface, not an engine statement** (§1, §3). No `CREATE DAG … STEP` | useful, clean code | Nova maintains a patch over the StarRocks grammar |
| **D9.4** | **Delegate-first**: run `SUBMIT TASK` on the *submitter's own connection*, so StarRocks RBAC is the enforcement. Nova executes directly only what the engine cannot | clean architecture, useful | MVP cannot run arbitrary DDL as a task (see E2) |
| **D9.5** | Phase 1 = cron + DAG + SQL-defined + metadata + engine. **Stream is Phase 2**, and ships with **one** provider (`mv_refresh`) | useful, resource | The user's "has stream" request lands later than the rest |
| **D9.6** | `partition_change` is **live**, watermarking on `SHOW PARTITIONS.VisibleVersion` (per-partition, monotonic). **Use `SHOW PARTITIONS`, never `information_schema.partitions`** | useful, performance | One metadata sweep per evaluation, and a measured cost ceiling (see §6) |
| **D9.7** | Run history snapshot into `NOVA_SYSTEM` — but as **DAG state**, not as a 24 h-loss mitigation | clean architecture | Some duplication of native `task_runs` |
| **D9.8** | Fix the `GET /tasks` root-connection RBAC defect **inside** Phase 9 | clean architecture | Slightly larger phase scope |

---

## 1. Engine facts that drove the design (all verified)

These were measured on the running engine. Several contradict the original
research premises, so they are stated first.

**Scope of these facts:** every row below describes the **engine** as it is on
`4.1.1` (`4.1.1-14b7e3f`). Where a row mentions the Nova `CREATE TASK` surface it
says so explicitly; that surface is *not* an engine feature. The distinction
matters because §3's grammar patch is the thing that makes `CREATE TASK` real, and
claiming otherwise would point 9b at a statement the parser does not have.

| Fact | Consequence for the design |
|---|---|
| **The only task statement the engine has is `SUBMIT TASK`.** `CREATE TASK` does not exist in 4.1.1 and is rejected in every form (`No viable statement for input 'CREATE TASK'`). `CREATE TASK … AFTER/FINALIZE/WHEN/OVERLAP_POLICY` is the **proposed Nova surface**, to be added by the 9b grammar patch; it is not an engine statement (see `docs/GUIDE_OBJECTS.md:748`). Nova's existing production path already builds `SUBMIT TASK` (`backend/app/modules/tasks/service.py:112-145`, `docs/08-task-manager.md`) | D9.3's lowering is `CREATE TASK …` (Nova-parsed) → `SUBMIT TASK` (engine) + Nova metadata. The grammar patch must target the existing `submitTaskStatement`/`taskClause`, not invent a statement the engine does not have |
| **No cron.** On `SUBMIT TASK`, `SCHEDULE = 'USING CRON …'` is rejected at parse time (`Unexpected input '='`). The schedule forms `SUBMIT TASK` accepts are `MANUAL`, `SCHEDULE EVERY(INTERVAL …)`, `SCHEDULE START('<literal>') EVERY(…)` | A cron parser and next-fire calculator are **Nova's**, not a translation layer. `SCHEDULE = 'USING CRON …'` in §3 is Nova-surface syntax (9b), never a string the engine receives |
| **`START` literals use the session timezone** (`Asia/Jakarta`), not UTC | The scheduler must store an explicit IANA timezone per task and never assume UTC |
| **`task_runs_ttl_second = 604800` (7 days)**, not 86400 | The "native history is lost in 24 h" premise was wrong; snapshotting is still useful, but for **graph state and lineage**, not data-loss |
| **No `AFTER` / `WHEN` / `FINALIZE` / `ALLOW_OVERLAPPING_EXECUTION`** — all rejected by the grammar | DAG edges, conditionals, finalizers and overlap policy are Nova metadata |
| **No completion hook; no nested `SUBMIT TASK`** (`Unexpected input 'SUBMIT'`) | Progress between nodes can **only** be observed by polling `information_schema.task_runs` |
| **TaskRun privileges are checked at `SUBMIT TASK`, against the submitter**, and recorded in `CREATOR`; the engine also **filters reads by caller privilege** | If Nova submits on the user's connection, RBAC enforcement is free (D9.4) |
| **The engine enforces the body restriction in the grammar**: only CTAS / INSERT / CACHE SELECT parse | "Body must be delegatable" is validated by the parser, not by Nova |
| **`information_schema.partitions` returns 0 rows for every schema** — it is an unpopulated MySQL-compatibility view on StarRocks. It also has no `DATA_VERSION` column | Never use it as a metadata source. The live surface is **`SHOW PARTITIONS`**, which exposes `VisibleVersion`, `VisibleVersionTime`, `VisibleVersionHash`, `DataVersion` |
| **`SHOW PARTITIONS.VisibleVersion` advances per-partition on every non-DDL load, and stays put on untouched partitions** (verified: insert into `p1` moved only `p1`; insert into `p2` moved only `p2`) | `partition_change` has a real, monotonic, per-partition watermark — it is live, and more precise than a table-level signal (D9.6) |
| **`information_schema.tasks` has no `STATE` column**; `SHOW TASKS` is not a statement | Suspend/resume state is not queryable; Nova derives it from the `SCHEDULE` string and its own records |
| **A task auto-pauses after `max_task_consecutive_fail_count = 10`** | A DAG can silently stop advancing; the reconciler must detect and surface this |
| **`task_check_interval_second = 60`** — the FE scans for due tasks once a minute | Native sub-minute cadence is not honoured; Nova's own tick is required for anything finer |
| **TaskRun rows cannot be deleted** — no `CLEAR TASK RUNS`, `DELETE` requires a where clause, and `DROP TASK` leaves the runs | Run history is append-only by construction; a Nova UI must not imply deletion |
| **A task that is running when the FE dies disappears from `task_runs` with no trace** (observed during an unintended FE restart) | `task_runs` is not a durable record of what ran. This is a second, independent justification for the Nova-side snapshot (§5), alongside the non-deletability above |
| **Periodic tasks survive an FE restart and resume ticking without `ALTER TASK RESUME`** (independently reproduced by two agents) | Nova need not "re-arm" schedules after a restart. What Nova *does* still need is reconciliation for runs whose trace it lost — the row above |

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

**Chosen: the Snowflake superset.** The block below is the **proposed Nova
surface** — it is not valid in StarRocks 4.1.1 today. Only the `SUBMIT TASK` forms
shown after it are engine statements.

```sql
-- Proposed Nova surface (requires the 9b grammar patch; NOT valid on 4.1.1)
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

The lowering D9.3 must be verifiable against what the engine actually accepts.
The equivalent engine statements the worker submits are:

```sql
-- What actually reaches the engine (SUBMIT TASK on the owner's connection)
SUBMIT TASK etl_root
  SCHEDULE START('2026-09-18 02:00:00') EVERY(INTERVAL 1 DAY)
  AS INSERT OVERWRITE agg_daily SELECT * FROM staging;

-- etl_clean and etl_notify have no engine-level DAG clause: their AFTER /
-- FINALIZE edges live in Nova metadata (CONFIG_TASK_EDGES), and Nova fires
-- them by polling information_schema.task_runs. Each still lowers to its own
-- SUBMIT TASK when its turn comes.
```

Two consequences worth stating plainly:

- The cron expression is **never sent to the engine**. `USING CRON` is rejected by
  `SUBMIT TASK` (`Unexpected input '='`), so Nova computes the next fire time
  itself and emits `SCHEDULE START('<literal>') EVERY(INTERVAL …)`, or a one-shot
  `SUBMIT TASK` for a single occurrence.
- `AFTER` / `FINALIZE` / `WHEN` / `OVERLAP_POLICY` have **no** engine counterpart —
  they are Nova grammar and Nova metadata. That is exactly why the lowering is
  Nova-owned, and why the 9b patch extends `submitTaskStatement` rather than
  translating to some engine DDL that does not exist.

`CREATE DAG … STEP …` was rejected: it forces Nova to maintain a grammar that is
*not* a superset of StarRocks forever, and it makes every future StarRocks grammar
re-sync a merge conflict by construction.

**Why the grammar cost is small** (verified against upstream, addendum §A): three
of the five new clauses need **no new lexer token** — `AFTER`, `WHEN` and
`SCHEDULE` already exist, and `AFTER`/`SCHEDULE` are already in `nonReserved`, so
they can be used as identifiers without conflict. `taskClause` is already
`taskClause*`, so the new clauses slot in as additional alternatives **inside**
`submitTaskStatement` without restructuring that rule. Only
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
The parse step is what 9b adds: the patch introduces the `CREATE TASK … AFTER /
FINALIZE / WHEN / SCHEDULE` surface into the **existing `submitTaskStatement`
rule** (its `taskClause*` is already variadic), so Nova keeps one statement parser
rather than a second DDL path. `docs/08-task-manager.md` documents the current
`SUBMIT TASK`-only path and is the reference for the statement the lowering must
produce.

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

**Effort is M, not S — worth stating plainly.** It is not a one-line swap of the
connection helper. `TaskService` opens its **own** connection per operation via
`_connect()`, so every method has to change shape to accept a connection injected by
the dependency layer. That refactor is the cost, and it is the reason to do it now
rather than later: once `create_task` runs on the caller's connection, Nova inherits
the engine's RBAC behaviour with **no additional authorization logic to write or
maintain**. The clean-architecture win is that a whole class of future defects
disappears by deletion, not by adding guards.

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

## 6. D9.5 / D9.6 — Stream triggers, and the provider that came back

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

### `partition_change` is live — correction to an earlier design decision

This decision was **reversed** after measurement. The earlier conclusion ("no usable
watermark source on 4.1.1") was based on `information_schema.partitions`, which is
an **unpopulated MySQL-compatibility view** — it returns 0 rows for every schema and
stays empty after `ANALYZE`. The live surface is **`SHOW PARTITIONS`**, which the
first probe never queried. `SHOW PARTITIONS.UPDATE_TIME` indeed only moves on DDL —
but `UPDATE_TIME` was the wrong column to test.

`SHOW PARTITIONS` exposes per-partition version columns, and `VisibleVersion` is the
watermark that works:

| Action | `p1` VisibleVersion | `p2` VisibleVersion |
|---|---|---|
| baseline | 1 | 1 |
| INSERT into `p1` only | **2** | 1 |
| INSERT into `p2` only | 2 | **2** |

Monotonic, and — critically — **per-partition**: writing to `p2` leaves `p1` alone.
So Nova learns *which* partition changed, not merely "something changed". This also
retires the composite `partition_set_hash + MAX(UPDATE_TIME)` design that existed
only to compensate for `UPDATE_TIME` being unreliable. The watermark is simply
`{partition_name → VisibleVersion}`, stored in `CONFIG_TASK_GRAPH_RUNS.wal_marks`.

### Measured performance, and the access pattern it forces

Measured on a real 5,000-partition table (built for this test, then dropped):

| Query | Result | Cost |
|---|---|---|
| `SHOW PARTITIONS FROM t` (full) | 5,001 rows, ~1.3 MB | **~110–260 ms** |
| `… WHERE PartitionName='p04999'` | 1 row | **~65–150 ms** |
| `… WHERE PartitionName LIKE 'p0000%'` | 10 rows | ~114 ms |

Two consequences, and the second corrects the research agent's assumption:

1. **A full sweep is acceptable at 5,000 partitions** (~110–260 ms, one round trip),
   so a discovery sweep is viable. This is no longer an unmeasured claim.
2. **Per-partition `WHERE` is *not* a cheap targeted read.** Each filtered query costs
   roughly the same as the full scan (~65–150 ms), because the filter is applied after
   the same metadata walk. Polling 500 partitions individually would cost ~30 s versus
   ~110 ms for one full scan. **The `WHERE` clause is therefore nearly useless, and the
   correct pattern is: one full `SHOW PARTITIONS` sweep, diff in Python.** Nova must not
   be designed around per-partition lookups.

Also note the filter grammar is limited: only `=`, `>=`, `<=`, `>`, `<`, `!=`, `LIKE`
are supported, and `IN (...)` is rejected (`Only operator =|>=|<=|>|<|!=|like are
supported`). This is another reason to sweep once and diff locally.

Reopen trigger: if a table reaches a partition count where a full sweep stops being
cheap (the measured 5,000-point is ~110–260 ms; the trend is roughly linear), revisit
— either with incremental discovery or by bounding the sweep.

### Provider staging

**`mv_refresh` remains the first provider**, because it is the only native
"data changed → work runs" primitive and its `SKIPPED` state is meaningful.
`partition_change` (now live, and the most precise for native partitioned tables) and
`load_event` (keyed on the globally unique `ID` from `information_schema.loads`)
follow. Task MV runs are identified by matching against
`information_schema.materialized_views`, **not** `LIKE 'mv-%'` — the naming
heuristic is fragile and mixes Nova tasks with MV tasks in the same view.

Still unverified, and not claimed: whether `VisibleVersion` moves when an MV refresh
writes, where `enable_task_history_archive` stores its archive and whether it is
queryable, and multi-FE leader failover (only single-FE restart has been observed).

---

## 7. Scope, sequencing, and what is still a human decision

### Proposed Phase 9 staging

| Stage | Content | Depends on |
|---|---|---|
| **9a** | Metadata tables, DAG validation, `nova-scheduler` tick + cron, `nova-worker`, delegate-first execution, reconciliation | — |
| **9b** | Grammar patch adding the `CREATE TASK … AFTER / FINALIZE / WHEN / SCHEDULE` surface to the **existing `submitTaskStatement` rule** (`taskClause*`, upstream `StarRocks.g4` pinned at `4.1.1`), plus the lowering of that surface to `SUBMIT TASK` + `CONFIG_TASK*` metadata; task graph UI | 9a |
| **9c** | Stream providers: `mv_refresh`, `partition_change` (`SHOW PARTITIONS` + `VisibleVersion`, one full sweep per evaluation), `load_event` | 9a |

9a is the vertical slice that makes the rest real. 9b is the user-visible surface.
9c is the feature the user asked for that the engine does not provide.

### Previously human-owned, now decided

These shaped the user-visible surface or the authorization model and were
confirmed on 2026-09-17. They are recorded here with their original framing so the
rationale survives; they are no longer open questions.

1. **E1 — confirm the SQL surface.** D9.3 recommends `CREATE TASK … AFTER …` over
   `CREATE DAG … STEP …`. This is the user's SQL, so it needs a yes. Note this is a
   **new Nova grammar surface**, not an engine statement — `CREATE TASK` does not
   exist in 4.1.1 and is added by the 9b patch over `submitTaskStatement` (§1, §3).
   **Confirmed 2026-09-17**: `CREATE TASK … AFTER / FINALIZE / WHEN /
   OVERLAP_POLICY` is the chosen surface.
2. **E2 — non-delegatable tasks.** With delegate-first (D9.4), Phase 1 supports
   task bodies the engine accepts. Tasks that need arbitrary DDL require a
   dedicated StarRocks service role — a change to the RBAC model, which is a
   human decision. Recommendation: **ship Phase 9 without it** and revisit only if
   a real need appears. The engine already enforces authorization at submit time for
   delegated tasks, so this no longer blocks Phase 1 on a security ground — it blocks
   only the DDL capability.
3. **E3 — the stream staging.** D9.5/D9.6 put stream in stage 9c, all three providers.
   If the user wants `has stream` earlier, `mv_refresh` + `partition_change` are the
   two that are proven to work today; `load_event` is the one still missing an
   internal-`INSERT` check.
4. **`SYSTEM$STREAM_HAS_DATA` naming** — keep the Snowflake-compatible name, or use
   a Nova name. Tied to E1; recommendation is to keep it, since parity is the goal.

### Reopen triggers

- StarRocks gains cron, `AFTER`, or a completion hook → delete the corresponding
  Nova component rather than maintain it.
- A partition count where a full `SHOW PARTITIONS` sweep stops being cheap → revisit
  the provider's access pattern (measured at ~110–260 ms for 5,000 partitions).
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
