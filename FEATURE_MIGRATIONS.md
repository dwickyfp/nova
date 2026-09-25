# FEATURE: Migrations

> Goal: **easy migration from a native StarRocks cluster into Nova** — databases,
> schemas, tables, views, functions, and eventually data — with a per-object
> report of what will move cleanly, what will move lossily, and what cannot move
> at all.

This document tracks the supported migration flow and its limits. Read the
"Current status" section before starting a cutover.

Related docs: `docs/28-migration-connector.md` (module spec),
`docs/21-backup-recovery.md` (#7 gate), `README.md` Phase 11 + Decision Log.

---

## 1. Current status (one paragraph)

An operator connects to a **source** StarRocks cluster, chooses the source
databases, reviews a dry-run and ordered plan, and can execute the migration
after opening the backup gate. Schema application and optional table-data copy
run in `nova-worker`; the API accepts the request and reports job progress.
Execution remains gated on issue **#7 (backup/restore)** and is off by default.

| Dimension | State |
|---|---|
| Source registration (host/port/user + secret ref) | ✅ Done |
| Object enumeration (8 families) | ✅ Done |
| Dry-run verdict + reason | ✅ Done |
| DDL collection (table/view/MV/function) | 🟡 Partial (task/pipe still blocked) |
| DDL retargeter (qualify + repoint + property remap) | ✅ Done |
| Apply planner (dependency-ordered, with blocked list) | ✅ Done |
| `/plan` endpoint (read-only; shows what apply would run) | ✅ Done |
| `/execute` endpoint (gated, per-object results, idempotent) | ✅ Done |
| Idempotency (`IF NOT EXISTS` guards) | ✅ Done |
| Acknowledgement + target confirmation gates | ✅ Done |
| Data movement (export → stage → import → verify) | ✅ Done |
| Database-level migration (schema + data) | ✅ Done |
| Worker execution and per-database job status | ✅ Done |
| **Preflight (privilege + shared-storage check)** | ✅ Done |
| **Task reconstruction (from `information_schema.tasks`)** | ✅ Done |
| RBAC / roles / grants migration | ❌ Out of scope (by design) |

Legend: ✅ done · 🟡 partial · ❌ not done.

---

## 2. What "easy migration" requires

The user-facing goal decomposes into six capabilities:

1. **Discover** — connect to the source and know what exists. ✅
2. **Assess** — tell the operator what will move and what will not. ✅
3. **Reconstruct** — obtain faithful, replayable DDL for every object. 🟡
4. **Apply** — create the objects on the Nova target. ✅ (gated)
5. **Move data** — copy rows for tables. ✅ (requires shared storage)
6. **Verify** — compare row counts and numeric digests after the move. 🟡

### Asynchronous batch flow

1. Enter the source host, MySQL port, user, and an optional configured secret
   reference.
   Nova does not accept a password value in the form or store one in
   `NOVA_SYSTEM`.
2. Use **Test connection** before saving the source. The worker authenticates
   to the unsaved address and runs `SELECT 1`; the test does not register the
   source. Saving is enabled only for the tested form values.
3. Save the source and list the databases visible to that source user. Select one or
    more databases to migrate. Review each database's dry-run, blocked objects,
    plan, and preflight before execution.
4. Submit `POST /api/v1/migration/execute-batch` with `databases: string[]`
   (1–50 unique names).
   The API responds `202` with a job identifier; `nova-worker` claims the job,
   applies each database, and persists per-database results. Poll
   `GET /api/v1/migration/jobs/{id}` to see completion, failures, and rows
   verified.

The backend does not apply DDL or copy rows in the HTTP request. Start
`python -m app.worker` alongside the backend, or enable the Compose `app`
profile. The backend and worker must share StarRocks, Redis, `SECRET_KEY`,
`FERNET_KEY`, and `nova.yaml`. For a password-protected source, configure the same secret
provider and access on the worker so it can resolve the source's `secret_ref`.
The job can only execute while the originating user session
is valid, and the worker rechecks that user's active role and StarRocks grants.

Current limits: the target is Nova's local StarRocks cluster; data copy requires
a stage reachable by both source and target; pipes, masking policies, and row
access policies remain blocked or skipped; roles and grants are not migrated.
Whole-database dry-run, plan, and execute omit global functions by default,
because they are cluster-wide objects. Enumeration still lists them; select a
global function explicitly to include it in a single-database operation.
There is no continuous sync or source-to-source target selection. Count and
digest verification are useful checks, not a byte-for-byte proof of every data
type. The `starrocks-cluster-sync` adapter only reports binary availability;
Nova's execution path uses its own SQL planner and data mover.
Jobs depend on a live originating session. If the session expires, the user
logs out, or the active security context changes before the worker claims the
job, the worker must refuse execution and report the failure.

---

## 3. Checklist — DONE

### 3.1 Core plumbing

- [x] Source cluster registry `NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES` — address
      only, **no password column** (`repository.py`).
- [x] Secret-reference credential model; password resolved at call time, fails
      closed (`source.py`).
- [x] Read-only source connection over the MySQL protocol (`source.py:
      open_source_connection`).
- [x] Enumerate/dry-run **fail closed** — unknown source is 404, missing source
      is 422, never a silent local-engine read (QA Finding 1).
- [x] Audit rows for `register_source` and `dry_run` (`NOVA_SYSTEM.AUDIT_LOG`).
- [x] Single credential redactor reused (`sql_guard.redact_sql_credentials`);
      every DDL passes through it before reaching a response.
- [x] `SanitizingJSONResponse` on every endpoint.
- [x] Engine adapter is **status-only** — no `subprocess`, no shell-out.
- [x] Execute remains behind `MIGRATION_EXECUTE_ENABLED` and explicit operator
      acknowledgement.

### 3.2 Object enumeration

- [x] Base tables via `information_schema.tables` (`TABLE_TYPE='BASE TABLE'`).
- [x] Views via `information_schema.views`.
- [x] Async MVs via `information_schema.materialized_views` (**never**
      `tables`, where StarRocks reports them as `VIEW`).
- [x] Database-scoped SQL functions via `SHOW FULL FUNCTIONS`.
- [x] **Global SQL functions via `SHOW FULL GLOBAL FUNCTIONS`** (added).
- [x] Tasks via `information_schema.tasks` (best-effort).
- [x] Pipes via `information_schema.pipes` (best-effort).
- [x] Masking / row-access policies via `SHOW ... POLICIES` (best-effort).

### 3.3 DDL / definition collection

- [x] Table DDL via `SHOW CREATE TABLE`, redacted.
- [x] View DDL via `SHOW CREATE VIEW`, redacted.
- [x] Async MV DDL via `SHOW CREATE MATERIALIZED VIEW`, redacted.
- [x] **SQL function reconstruction** from `SHOW FULL FUNCTIONS`
      (`verdicts.reconstruct_sql_function_ddl`) — no `SHOW CREATE FUNCTION`
      exists on 4.1.4, so the body (the `Properties` column) is rebuilt into a
      `CREATE FUNCTION` statement; argument names are inferred from the body's
      backticked identifiers (added).
- [x] Global function DDL reconstructed with `CREATE GLOBAL FUNCTION`.
- [x] Function reconstruction verified to **round-trip on the real engine**
      (created on a target and evaluated identically).

### 3.4 Retargeter + apply planner (11-B groundwork)

- [x] **Production DDL retargeter** (`retarget.py`) — qualifies the created object
      with the target database, repoints internal `source_db.` references,
      preserves semantic clauses (`SECURITY`, `REFRESH`, `PARTITION BY`,
      `DISTRIBUTED BY`), and strips deployment-specific `PROPERTIES`
      (`replication_num`, `replicated_storage`, `storage_medium`, `compression`,
      `fast_schema_evolution`, …). Refuses with `RetargetError` rather than
      mis-rewriting.
- [x] **Replication remap** — `replication_num` is rewritten to the target's safe
      backend count when known (a multi-BE source into a 1-BE target no longer
      produces an unplaceable table); dropped otherwise.
- [x] **Apply planner** (`planner.py`) — emits steps in dependency order
      (database → table → view → MV → function), re-numbers them sequentially,
      and reports objects with no usable definition as **blocked**, never
      silently dropped.
- [x] **`POST /api/v1/migration/plan`** — read-only; returns the ordered
      statements plus the blocked list and the current execute gate state.
- [x] **Target replication probe** — reads the local engine's alive-backend count
      (`repository.target_replication_num`).

### 3.5 Execute (11-B, gated on #7)

- [x] **`POST /api/v1/migration/execute`** — applies the plan to the target.
- [x] **Gate on `MIGRATION_EXECUTE_ENABLED`** (default **False**) — refuses with
      403 until the operator acknowledges a restorable backup exists.
- [x] **Omission acknowledgement** — `acknowledge_omissions=true` is mandatory.
- [x] **Target confirmation** — `confirmation` must equal the target database
      name when `MIGRATION_EXECUTE_REQUIRE_CONFIRMATION` is set.
- [x] **Runs as the caller** via the shared `query_service` pipeline, so
      StarRocks RBAC is the real authority and every statement is audited.
- [x] **Per-object results** — each step reports `ok` / `failed` with the engine
      error, so a partial run is fully visible.
- [x] **Idempotency** — every object step carries `IF NOT EXISTS`; a re-run
      creates nothing new and does not fail.
- [x] **Audit row** (`action="execute"`) with success/failure status.
- [x] **Frontend execute wizard** — plan preview, acknowledgement checkbox,
      execute, per-object result table.

### 3.6 Data movement (11-C)

- [x] **`include_data` option on execute** — schema first, then rows.
- [x] **Export → stage → import** using the storage-agnostic `FILES()` path:
      `INSERT INTO FILES(...)` on the source, `INSERT INTO target SELECT ...
      FROM FILES(...)` on the target. Reuses the same FILES() parameter set as
      `@stage` queries.
- [x] **`data_mover.py`** — pure SQL builders + a `TableCopyPlan` (export /
      import / count / digest), `stage_path_for` scoping, column listing.
- [x] **Explicit column list** from `information_schema.columns`, so the copy is
      independent of physical column order.
- [x] **Verification** — row count on both sides plus an order-independent
      `SUM(CAST(numeric AS DOUBLE))` digest; a mismatch is reported, not hidden.
- [x] **Per-table result** — rows exported/imported, verified, digest match,
      errors — with `rows_moved` only counting verified copies.
- [x] **Source-scoped stage path** — `migration-staging/<run-id>/<db>/<table>` so
      concurrent migrations do not collide.
- [x] **Engine-reachable endpoint** — the host-side S3 endpoint is rewritten for
      the Docker engine (`to_docker_endpoint`), with `enable_path_style_access`.
- [x] **Frontend** — "also copy table data" option and a per-table data result
      table.

### 3.7 Preflight (fail fast)

- [x] **`preflight.py`** — pure grant parser + analyzer: what the plan needs vs
      what the caller holds, with the reason each privilege is needed.
- [x] **`POST /api/v1/migration/preflight`** — read-only; reports missing
      privileges and, for data movement, whether a transfer stage is configured.
- [x] **Execute runs the preflight first** (configurable via
      `MIGRATION_EXECUTE_PREFLIGHT`, default on) and refuses with **409** rather
      than half-applying a plan. The check reuses the exact plan about to run.
- [x] **Only relevant privileges are required** — a tables-only plan does not
      demand `CREATE VIEW`/`CREATE FUNCTION`.
- [x] **Scope-aware** — `ALL DATABASES` vs `DATABASE <name>` vs catalog grants.
- [x] **Frontend** — "Check preflight" button with a per-privilege result table.

### 3.8 Task reconstruction

- [x] **`information_schema.tasks` used correctly** — the filter column is
      `DATABASE` (not `DATABASE_NAME`, which the earlier code used and which
      silently returned nothing), and `SCHEDULE` + `DEFINITION` are read.
- [x] **`reconstruct_task_ddl`** — builds `CREATE TASK` from schedule + body
      (+ `PROPERTIES` when present); returns `None` when either is missing rather
      than emitting an invalid statement.
- [x] **Task retargeting + planning** — `CREATE TASK` is a first-class plan step
      (after functions), retargeted to the target database.
- [x] Task stays `lossy` — the exact original statement and properties may differ.
- [x] Pipe stays `lossy` with **no** definition: `information_schema.pipes` has
      no SELECT body, so it cannot be reconstructed.

### 3.9 Verdict rules (domain core, pure functions)

- [x] Table → `migratable` (with distribution remap notes).
- [x] View → `migratable`.
- [x] Async MV → `migratable`; sync MV → `lossy`.
- [x] SQL function → `lossy` (arg names inferred — no faithful surface).
- [x] Non-native function (Java/Python) → `lossy`.
- [x] Task / Pipe → `lossy` (no `SHOW CREATE`).
- [x] Masking / row-access policy → `skipped` (no DDL export in 4.1.4).

### 3.10 Tests

- [x] Unit suite `tests/unit/test_migration_connector.py` — 60 tests, no engine.
- [x] Unit suite `tests/unit/test_migration_retarget.py` — 30+ tests across the
      retargeter, planner, and `/plan` HTTP contract, using DDL captured verbatim
      from 4.1.4.
- [x] L3 suite `tests/integration/test_migration_l3.py` against real 4.1.4.
- [x] **Schema round-trip harness** — reconstructs DDL from dry-run `detail`,
      applies it to a fresh target, and compares `SHOW CREATE` (table, view) and
      behavior (function).
- [x] **Plan-apply end-to-end** — requests `/plan`, runs every step against a real
      target in order, and verifies the target matches the source.
- [x] **Execute end-to-end** — with the gate open, `/execute` creates the target
      objects through the query pipeline and the target schema matches.
- [x] **Execute idempotency** — a second `/execute` run succeeds without error.
- [x] **Execute gate refusals** — 403 while disabled, 422 without
      acknowledgement / with a wrong confirmation.
- [x] **Data mover unit suite** — SQL builders, path scoping, column/digest
      logic, and execute orchestration with fakes.
- [x] **Data movement end-to-end** — export → import → verify on a real engine,
      asserting row count and digest match.
- [x] **Preflight unit suite** — grant parsing, scope matching, relevant-only
      requirements, and the HTTP contract.
- [x] **Preflight end-to-end** — passes for a privileged caller, reports a
      missing privilege, and blocks execute with 409.
- [x] **Task reconstruction unit tests** — DDL shape, properties, and the
      missing-definition refusal.
- [x] **Data-fidelity harness** — row-count + order-independent digest checks
      for the current data mover.
- [x] Credential non-leak assertions (response, audit, registry).
- [x] Execute-gate refusal tests.

### 3.11 Frontend

- [x] Migration page: source connection form, database discovery and selection,
      per-database review, acknowledgement, and job progress.
- [ ] Source deletion / edit UI.
- [x] Database picker with multiple selection.
- [ ] Object-selection UI (dry-run currently assesses everything).
- [ ] DDL preview pane per object.
- [x] Progress and acknowledgement UI for batch execute.

---

## 4. Checklist — TODO (what still has to be built)

Grouped by the roadmap phases. 11-B and 11-C are the real "migration"; 11-A is
what exists.

### 11-B — Execute / cutover (gated on #7 backup/restore)

- [x] **DDL retargeter (production).** `retarget.py` qualifies the created object
      with the target DB, rewrites internal `source_db.` references, preserves
      semantic clauses, strips deployment `PROPERTIES`, and remaps
      `replication_num` to the target's safe factor.
- [x] **Apply planner in dependency order** — database → tables → views → MVs →
      functions, with a blocked list for objects that cannot be produced.
- [x] **`CREATE DATABASE` step** — planned as the first step
      (`CREATE DATABASE IF NOT EXISTS`).
- [x] **Read-only `/plan` endpoint** — operator can review the exact statements
      and the blocked list.
- [x] **Execute endpoint** (`POST /api/v1/migration/execute`) behind an explicit
      gate (`MIGRATION_EXECUTE_ENABLED`, default off).
- [x] **Idempotency / resumability** — every object step carries `IF NOT EXISTS`
      and the database step too; a re-run creates nothing and does not fail.
- [x] **Omission acknowledgement** — `acknowledge_omissions=true` is required.
- [x] **Per-object execute results** — status + engine error per step.
- [x] **Frontend execute wizard** — plan preview, acknowledgement, run, results.
- [ ] **`ACCOUNTADMIN` guard in the execute path** — the connector does not
      replicate roles at all (RBAC is out of scope), so there is nothing to
      guard yet; add the guard the moment any role replay is introduced.
- [ ] **Backup gate wiring beyond the flag** — the flag is the operator's
      acknowledgement; wiring it to an actual snapshot inventory (#7) is pending
      #7 landing.
- [x] **Privilege preflight** — checks required target grants before executing;
      `GRANT ALL ON *.*` does not include `CREATE DATABASE`, which needs a
      catalog-scope grant.

### 11-C — Data movement

- [x] **Table data copy** via `INSERT INTO FILES()` export on the source and
      `INSERT INTO target SELECT FROM FILES()` import on the target.
- [x] **Database-level migration** — schema + data in one `include_data` run.
- [x] **Schema only vs schema + data** toggle (`include_data`).
- [x] **Verification pass** — count + digest per table after the move.
- [x] **Shared-storage preflight** — the preflight reports whether transfer
      credentials are configured and resolve, before any export runs.
- [ ] **Data size / row-count preflight** so the operator sees the volume before
      a multi-TB move (row counts are read during the copy, but not previewed).
- [ ] **Chunked / parallel copy** with retry for large tables. The copy is
      currently one export+import per table; a multi-TB table is not split.
- [ ] **Incremental / continuous sync** — not planned for v1.
- [ ] **Prove the engine can reach the bucket** — the preflight checks config,
      not reachability (that requires the engine to attempt a read).

### DDL coverage gaps

- [x] **Task DDL reconstruction** — builds `CREATE TASK` from the schedule and
      body in `information_schema.tasks` when both are available; still lossy.
- [ ] **Pipe DDL reconstruction** — same; the `SELECT` body is unavailable, so
      this may stay lossy.
- [ ] **Materialized view dependencies** — MV DDL references base tables; apply
      ordering must account for it (part of 11-B ordering).
- [ ] **Column-level fidelity** — verify `COMMENT`, `DEFAULT`, generated columns
      survive the round-trip (partial: L3 compares name/type/key only).

### Policy / security objects

- [ ] **Masking policy export** — impossible on 4.1.4; keep `skipped`. Revisit
      if a future engine version adds DDL export.
- [ ] **Row-access policy export** — same.
- [ ] **RBAC users/roles/grants** — out of scope by design (passwords are never
      exportable). Documented; not planned.

### Multi-source / UX

- [ ] **Source delete + edit** endpoints and UI.
- [x] **List databases on a source** through the worker and present multiple
      selection in the UI.
- [ ] **Cross-source migration** (source A → target B where target is another
      registered source, not just the local engine).
- [ ] **Migration history** — a first-class record of what was migrated, when,
      and with what verdicts (today only flat audit rows).
- [ ] **Scheduled / repeatable sync** (continuous replication) — not planned in
      v1; evaluate after 11-C.

### Engine / distribution

- [ ] **`starrocks-cluster-sync` integration** — adapter exists as a status check
      only. If adopted for data movement, decide invoke-vs-reimplement (license
      is undeclared → invoke only).
- [ ] **Storage-volume mapping** on the target.
- [ ] **External-catalog migration** — metastore credentials are never
      exportable; decide report-only vs manual re-entry.

---

## 5. Object support matrix

"Plan" = the object produces an executable, retargeted step. "Execute" = an
apply endpoint runs it (still gated). "Data" = rows are copied (11-C).

"Plan" = the object produces an executable, retargeted step. "Execute" = the
`/execute` endpoint runs it (when the operator opens the gate). "Data" = rows are
copied (`include_data`).

| Object | Enumerate | DDL collected | Verdict | Plan | Execute | Data |
|---|---|---|---|---|---|---|
| Database | ❌ | n/a | n/a | ✅ (`CREATE DATABASE`) | ✅ | n/a |
| Base table | ✅ | ✅ | `migratable` | ✅ | ✅ | ✅ |
| View | ✅ | ✅ | `migratable` | ✅ | ✅ | n/a |
| Async MV | ✅ | ✅ | `migratable` | ✅ | ✅ | n/a |
| Sync MV | 🟡 (engine-limited) | 🟡 | `lossy` | 🟡 (if DDL present) | 🟡 | n/a |
| SQL function (db) | ✅ | ✅ | `lossy` | ✅ | ✅ | n/a |
| SQL function (global) | ✅ | ✅ | `lossy` | ✅ | ✅ | n/a |
| Java/Python UDF | ✅ | ❌ | `lossy` | ❌ blocked | ❌ | n/a |
| Task | ✅ | ✅ (reconstructed) | `lossy` | ✅ | ✅ | n/a |
| Pipe | ✅ | ❌ | `lossy` | ❌ blocked | ❌ | n/a |
| Masking policy | ✅ (best-effort) | ❌ | `skipped` | ❌ blocked | ❌ | n/a |
| Row-access policy | ✅ (best-effort) | ❌ | `skipped` | ❌ blocked | ❌ | n/a |
| Users / roles / grants | ❌ | ❌ | out of scope | ❌ | ❌ | n/a |

---

## 6. Engine facts (verified against StarRocks 4.1.4)

These were probed on a live engine and drive the verdict rules:

- **`SHOW CREATE FUNCTION` does not exist.** SQL UDF definitions are only
  available as `SHOW FULL FUNCTIONS` rows: `Signature` holds argument *types*
  (`f_add(INT,INT)`), `Properties` holds the body (`` `x` + `y` ``).
- **Argument *names* are not exposed by any surface.** `information_schema.
  routines` is empty for SQL UDFs. Reconstruction infers names from the body's
  backticked identifiers, which is why the verdict is `lossy`, not `migratable`.
- **`SHOW FULL GLOBAL FUNCTIONS`** is the definition surface for global SQL UDFs.
- **`SHOW CREATE TABLE` / `SHOW CREATE VIEW` emit unqualified names**, while
  bodies keep qualified source-database references.
- **A source table's replication factor can exceed the target's backend count.**
  `SHOW CREATE TABLE` may omit `replication_num` entirely, in which case dropping
  it lets the target's *default* (e.g. 3) apply and fail on a 1-BE target. The
  planner therefore rewrites it to the target's safe factor when known.
- **`SHOW CREATE MATERIALIZED VIEW`** is the only MV surface carrying `REFRESH`,
  `PARTITION BY`, and `PROPERTIES`.
- UDFs require `enable_udf=true` in `fe.conf`; the L3 function test skips when
  the engine has them disabled.
- **Async MV** DDL round-trips; **sync MV** definition is lossy on every surface.
- **Masking / row-access policies** have no DDL export.
- **`GRANT ALL ON *.*` covers table-level ops only.** Creating databases/tables/
  views/MVs needs explicit grants: `GRANT CREATE DATABASE ON CATALOG
  default_catalog`, `GRANT CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW ON
  ALL DATABASES`. A caller without them gets a per-object RBAC denial at execute.
- **`FILES()` needs an engine-reachable endpoint and path-style access.** The
  host-side `http://127.0.0.1:<port>` must be rewritten to the Docker service
  name (`minio:<port>`), with `aws.s3.enable_path_style_access='true'` and
  `aws.s3.use_aws_sdk_default_behavior='false'` — the same set `@stage` queries
  emit. Without path-style, StarRocks tries virtual-host addressing
  (`<bucket>.minio`) and fails DNS.
- **`FILES()` does not expand a bare directory.** A read must use a glob
  (`.../table/*.parquet`) or an exact file; `.../table/` returns "No files were
  found" even when files exist. The mover writes to a directory and reads with a
  glob.
- **Data movement requires shared object storage.** The export runs on the source
  and the import on the target; both must reach the same stage. Nova does not
  create a path between two isolated clusters.
- **`information_schema.tasks` uses `DATABASE`, not `DATABASE_NAME`.** The column
  set is `TASK_NAME, CREATE_TIME, SCHEDULE, CATALOG, DATABASE, DEFINITION,
  PROPERTIES, CREATOR`. A query filtered on `DATABASE_NAME` fails to resolve and
  silently yields nothing.
- **Some 4.1.4 builds disable tasks entirely** (`CREATE TASK` / `SHOW TASKS` are
  syntax errors). Task enumeration and reconstruction are best-effort; the L3
  tests skip when the engine does not expose the feature.
- **`information_schema.pipes` has no SELECT body.** Its columns are
  `PIPE_NAME, PROPERTIES, STATE, TABLE_NAME, …` — nothing to reconstruct the
  ingest definition, so a PIPE is honestly reported as not offered.

---

## 7. How to verify (tests)

```bash
# Unit (no engine)
cd backend && uv run pytest tests/unit/test_migration_connector.py \
    tests/unit/test_migration_retarget.py -q

# L3 against a real engine on port 29030
cd backend && NOVA_ORCH_SR_PORT=29030 uv run pytest tests/integration/test_migration_l3.py -q

# Opt-in: source cluster 29030, separate target test stack on 39030
cd backend && NOVA_MIGRATION_SOURCE_PORT=29030 \
  NOVA_TEST_FE_MYSQL_PORT=39030 NOVA_TEST_FE_HTTP_PORT=38030 \
  NOVA_TEST_FE_ARROW_PORT=39408 NOVA_TEST_MINIO_PORT=39000 \
  NOVA_TEST_REDIS_PORT=36379 \
  uv run pytest tests/integration/test_migration_worker_cross_cluster.py -q
```

The L3 schema round-trip tests are the acceptance gate for 11-B: a `migratable`
verdict that cannot be replayed to a target is a false positive in the report.
The existing `test_migration_l3.py` registers the test engine as both source
and target. `test_migration_worker_cross_cluster.py` starts a separate
`app.worker` process, submits one two-database batch, and verifies schema and
copied rows on a distinct target cluster. It skips unless
`NOVA_MIGRATION_SOURCE_PORT` is set, so a skipped run is not evidence of a
working cross-cluster transfer.
Report skipped tests explicitly, especially the data-copy case that skips when
the test object store cannot be reached.
The data-fidelity tests are the acceptance gate for 11-C.

> **Test-stack note:** if the shared stack's BE is not running
> (`docker ps` shows `backend-starrocks-be-1` as `Created`), every L3 test errors
> with `Cluster has no available capacity`. Start it with
> `docker start backend-starrocks-be-1` before running.

---

## 8. Decisions that shape this feature

| Decision | Reason | Reopen trigger |
|---|---|---|
| **v1 is assessment + dry-run only; execute is gated on #7** | Cutover without a restorable backup is irreversible | #7 lands and an execute decision is made |
| **The report is the product; declared omissions are not bugs** | A silent drop (a vanished policy) is worse than an explicit `skipped` | — |
| **No `SHOW CREATE FUNCTION`; SQL UDFs are `lossy`** | Argument names are unrecoverable, so reconstruction may differ | An upstream surface exposes the full definition |
| **The engine binary is invoked, never bundled** | `starrocks-cluster-sync` license is undeclared → all-rights-reserved | Written redistribution permission from the vendor |
| **Source is a cluster address, not a storage connection** | Storage-connection identity is object storage, not a StarRocks endpoint | A remote connection cannot be reached over the MySQL protocol |
| **Same database name on source and target is allowed** | They are different clusters; refusing same-name would block the common "keep the DB name" migration | The retargeter still qualifies and strips properties, so same-name is not a no-op | A target-side naming conflict is discovered at execute time |
| **`replication_num` is remapped, not blindly dropped** | Dropping it lets a target default (often 3) apply and fail on a small cluster | The planner probes the target's backend count; unknown target → drop | The target cannot be probed and an unsatisfiable default applies |
| **`/plan` is read-only and separate from execute** | The operator must be able to review the exact statements before any cutover, and review must not require the execute gate to open | Two endpoints instead of one; the plan must be recomputed at execute time | Execute incorporates planning so a stale plan cannot be applied |
| **Execute is off by default and gated by `MIGRATION_EXECUTE_ENABLED`** | Cutover without a restorable backup is irreversible; the flag is the operator's explicit acknowledgement (#7) | The operator owns the backup guarantee — the flag is a gate, not a safety mechanism Nova can enforce | #7 lands and the flag is replaced by a real snapshot check |
| **Execute runs as the caller, never root** | StarRocks RBAC must be the real authority, and each statement must be audited under the actor | A caller without target grants gets per-object denials rather than a privileged success; `GRANT ALL ON *.*` is insufficient (`CREATE DATABASE` is a separate grant) | A dedicated migration service account is introduced with a scoped grant set |
| **Every execute step is idempotent (`IF NOT EXISTS`)** | A migration is expected to be re-runnable after a partial failure | An existing object is not altered — a re-run only fills gaps | A target object must be reconciled/updated, not left as-is |
| **Data moves through a shared stage, not a direct cluster link** | Reuses Nova's existing storage abstraction and needs no new network path or driver; the source and target both already speak `FILES()` | Requires the operator to share object storage between the clusters; a JDBC external catalog would need a driver jar the engine does not bundle | The clusters share no storage and the operator accepts the JDBC-driver setup |
| **Copy is per-table, export-then-import, with count + digest verification** | A visible, order-independent check is the difference between "copy ran" and "data arrived" | A large table is not chunked yet; the whole table is one export/import | A multi-TB table must be split with retry |
| **Data movement is opt-in (`include_data`), default schema-only** | Schema-only is the safe default; moving data is a heavier, storage-dependent operation | Two run shapes to reason about | Data movement becomes the default expectation |
| **Preflight runs inside execute and refuses with 409** | A half-applied migration (some objects created, others denied) is worse than a refusal that names the missing grant | Execute does one extra read (`SHOW GRANTS`) and can be blocked by a stale grant read | Preflight proves too slow or too often wrong on a real deployment |
| **Only plan-relevant privileges are required** | Telling an operator to grant `CREATE VIEW` for a tables-only run is noise that erodes trust in the check | The requirement set must track the plan shape | A new object kind is added without updating `required_privileges` |

---

## 9. Suggested build order (recommended)

1. ~~**DDL retargeter in production** + database creation.~~ ✅ Done.
2. ~~**Apply objects in dependency order** (planner).~~ ✅ Done.
3. ~~**Execute endpoint** behind the #7 gate + acknowledgement + idempotency.~~
   ✅ Done.
4. ~~**Frontend execute wizard.**~~ ✅ Done.
5. ~~**Data movement** (11-C) with verification.~~ ✅ Done.
6. ~~**Privilege + shared-storage preflight.**~~ ✅ Done.
7. ~~**Task DDL reconstruction.**~~ ✅ Done (task now executable; pipe stays
   blocked, the engine exposes no body).
8. **Chunked / parallel copy** with retry for large tables.
9. **Engine-reachability probe** for the transfer bucket in preflight.
10. **Incremental / continuous sync** — evaluate after 11-C proves stable.
