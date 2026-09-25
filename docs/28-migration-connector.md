# Module 28: Migration Connector

> Connect to a source StarRocks cluster, select databases, review a plan,
> and execute the migration in `nova-worker`.

---

## Status

Source discovery, dry-run, planning, preflight, and gated execution are
implemented. The operator first enters the source address and optional secret reference,
then selects one or more databases returned by that source. The API persists
job state in `NOVA_SYSTEM`; `nova-worker` performs source reads and target
mutations. The execute gate (`MIGRATION_EXECUTE_ENABLED`) is off by default
until an operator has a restorable target backup for issue #7.

| Capability | v1 |
|---|---|
| Register a **source cluster** (host / port / username + secret reference) | ✅ |
| Enumerate databases / tables / views / MVs / functions / tasks / pipes / policies **on that source** | ✅ |
| Dry-run verdict per object (`migratable` / `lossy` / `skipped`) with reason | ✅ |
| Reconstruct DDL for table / view / MV / SQL function (db + global) | ✅ |
| Retarget DDL for a target database (qualify, repoint, strip/map properties) | ✅ |
| Dependency-ordered apply **plan** with a blocked list | ✅ |
| Audit rows for source registration, dry-run, and plan | ✅ |
| Detect the operator-provided `starrocks-cluster-sync` binary | ✅ (status only) |
| Batch execute with per-database progress | ✅ gated on #7 |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/migration/capabilities` | Declares implemented phases and current execute gate |
| GET | `/api/v1/migration/engine` | Binary availability (no execution) |
| GET | `/api/v1/migration/sources` | List registered source clusters |
| POST | `/api/v1/migration/sources/test` | Test an unsaved source in the worker with a read-only `SELECT 1` |
| POST | `/api/v1/migration/sources` | Register a source cluster by address |
| POST | `/api/v1/migration/databases` | List databases visible on the registered source |
| POST | `/api/v1/migration/enumerate` | Enumerate a database's objects on a **registered source** |
| POST | `/api/v1/migration/dry-run` | Classify objects on a **registered source**; read-only |
| POST | `/api/v1/migration/plan` | Build a dependency-ordered apply plan; read-only |
| POST | `/api/v1/migration/preflight` | Check target privileges and shared storage; read-only |
| POST | `/api/v1/migration/execute` | Accept one database and return `202` + job ID; **gated on #7** |
| POST | `/api/v1/migration/execute-batch` | Accept 1–50 selected databases; return `202` and a job ID |
| GET | `/api/v1/migration/jobs/{id}` | Read job and per-database progress/results |

`source` is required on enumerate and dry-run. If it is missing the request is
rejected (`422`) and if it is unknown the request returns `404`; the local engine
is never used as a silent fallback.

The backend validates and submits a batch. The worker claims its job from
`NOVA_SYSTEM.CONFIG_MIGRATION_JOBS`, checks the originating session and active
security context in Redis, then handles the selected databases in order. A
database can fail while others report their own outcomes; the job can finish
`succeeded`, `partial`, `failed`, or `interrupted`. Poll `GET /jobs/{id}` after
the `202` response. No password or encrypted password is stored in the job row.
Source connection tests, database discovery, enumerate, dry-run, plan, and preflight also run on the
worker; their HTTP routes wait a bounded time for the read result.
For password-protected sources, the worker needs access to the same secret
provider configuration as the backend; it resolves `secret_ref` when it opens
the source connection.
Whole-database dry-run, plan, and execute omit cluster-wide global functions
by default. Enumeration lists them, and a single-database request can select
one explicitly.

## Verdict rules

The rules live in `backend/app/modules/migration/verdicts.py` as pure functions
with no I/O. They are grounded in the NOVA-84 research on StarRocks 4.1.4
(commit pin `4a9848ed`).

| Object | Surface | Verdict |
|---|---|---|
| Base table | `information_schema.tables` (`TABLE_TYPE = 'BASE TABLE'`) | `migratable` (distribution props remapped) |
| View | `information_schema.views` + `SHOW CREATE VIEW` | `migratable` |
| Async materialized view | `information_schema.materialized_views` + **`SHOW CREATE MATERIALIZED VIEW`** | `migratable` |
| Sync materialized view | same surface | **`lossy`** — the statement emits only `CREATE MATERIALIZED VIEW ... AS SELECT` |
| Function (native SQL) | `SHOW FULL FUNCTIONS` | `lossy` — argument names are inferred |
| Function (non-native body) | `SHOW FULL FUNCTIONS` | `lossy` |
| TASK | `information_schema.tasks` | **`lossy`** — no `SHOW CREATE`; DDL is reconstructed |
| PIPE | `information_schema.pipes` | **`lossy`** — no `SHOW CREATE`; the `SELECT` body is lost |
| MASKING POLICY | `SHOW MASKING POLICIES` | **`skipped`** — no DDL export in 4.1.4 |
| ROW ACCESS POLICY | `SHOW ROW ACCESS POLICIES` | **`skipped`** — no DDL export in 4.1.4 |

**Why `skipped` is not an error.** Masking and row-access policies have no DDL
export at all. If they were silently dropped, an operator would believe the
migration was complete while the policies vanished. They are reported as
`skipped` with an explicit reason — the report exists to make that visible.

**Materialized views.** MVs are enumerated from
`information_schema.materialized_views`, **never** from
`information_schema.tables` (where StarRocks reports them as `VIEW`, which loses
their identity). Async MV DDL is read through `SHOW CREATE MATERIALIZED VIEW`,
the only surface that carries `REFRESH`, `PARTITION BY` and `PROPERTIES`.
`SHOW CREATE VIEW` on an async MV drops all three and is not used.

## Credential security

The invariant is AGENTS.md §2: credentials never appear in an API response, log,
`NOVA_SYSTEM`, audit row, exception message, or frontend state.

- **A source is addressed by host/port/username plus an optional secret
  *reference*.**
  `NOVA_SYSTEM.CONFIG_MIGRATION_SOURCES` has no password column; the password is
  fetched from the configured secret provider at call time
  (`app.modules.migration.source`) and lives in memory only. A broken reference
  fails closed and never falls back to another principal. The frontend never
  sends a password.
- **Every DDL string is filtered** through the existing
  `sql_guard.redact_sql_credentials` before it reaches a response. There is no
  second redactor (Ruling 3 hardening): one rule, one implementation.
  `SHOW CREATE MATERIALIZED VIEW` has no `hidePassword` equivalent, so a future
  or custom MV property holding a secret would be emitted verbatim — the
  Nova-side filter is what closes that gap. It fails closed: a statement the
  redactor refuses becomes a placeholder, never the raw string.
- **`SanitizingJSONResponse`** is the response class on every endpoint, so a
  statement that somehow escaped the service is stripped at the boundary.

## Engine adapter (`starrocks-cluster-sync`)

`backend/app/modules/migration/engine.py` only checks whether an optional
operator-provided binary exists. It never executes the binary, uses
`subprocess`, or shells out. The current migration path uses Nova's SQL planner
and data mover.

The binary's license is **not declared** (no `LICENSE`, no pom `<licenses>`, no
public source repo → all-rights-reserved by default). Nova never bundles or
redistributes it. An operator may point `MIGRATION_CLUSTER_SYNC_BINARY` at a
local copy for status reporting; a missing binary is a typed, non-fatal
condition and does not block Nova's assessment or execute path.

## Lossy / skipped list (v1)

This is the report the operator reviews before cutover:

- **Skipped (cannot migrate):** `MASKING POLICY`, `ROW ACCESS POLICY`.
- **Lossy (migrates with loss):** `TASK` (definition reconstructed), `PIPE`
  (`SELECT` body lost), sync materialized views (no `REFRESH`/`PROPERTIES`),
  SQL functions (argument names are inferred — no `SHOW CREATE FUNCTION` exists),
  non-native UDF bodies (jar/payload not carried).
- **Not migrated by this connector at all:** RBAC users/roles/grants (passwords
  are never exportable), `ACCOUNTADMIN` (**must not** be replicated), external
  catalog credentials, resource groups, storage volumes, session variables.

## Apply planning (`plan`)

`retarget.py` and `planner.py` turn the collected definitions into an executable,
dependency-ordered plan. Both are **pure** — no I/O, no execution.

**Retargeting** (`retarget.py`) rewrites a source `SHOW CREATE` statement for the
target:

- **Qualifies** the created object with the target database. `SHOW CREATE` emits
  an unqualified name (``CREATE TABLE `t` …``); internal references keep the
  source database.
- **Repoints** `source_db.object` references to the target. A reference to any
  other database is left untouched — an external dependency, not something Nova
  invents.
- **Preserves semantic clauses**: `SECURITY`, `REFRESH`, `PARTITION BY`,
  `DISTRIBUTED BY`, `DUPLICATE KEY`.
- **Strips deployment-specific `PROPERTIES`** (`replication_num`,
  `replicated_storage`, `storage_medium`, `compression`,
  `fast_schema_evolution`, …). `replication_num` is special: it is **remapped** to
  the target's safe backend count when known (a multi-BE source must not produce
  a table a 1-BE target cannot place); otherwise it is dropped.
- **Refuses** with `RetargetError` when a statement cannot be retargeted with
  confidence. A silently mis-rewritten statement is worse than one the operator
  fixes by hand.

**Planning** (`planner.py`) emits steps in dependency order:

```
database → table → view → materialized_view → function
```

A view may reference a table or another view; an MV is built on tables; a
function may be called from a body. Objects with no usable definition (some
tasks, pipes, policies, non-native UDFs) are reported as **blocked** with a reason —
never silently dropped.

The plan is surfaced by `POST /api/v1/migration/plan`. The batch apply path is
`POST /api/v1/migration/execute-batch`, described below.

## Execute (gated on #7)

`POST /api/v1/migration/execute-batch` accepts 1–50 unique source database
names and responds `202` with a job ID. The worker builds and applies each
database's plan against Nova's local target, then persists each database's
status and counts. `GET /api/v1/migration/jobs/{id}` reports `queued`,
`running`, `succeeded`, `partial`, `failed`, or `interrupted` and the current
database. A single-database `POST /execute` compatibility route also queues a
worker job and responds `202`.

Three gates are enforced before submission and again when needed in the
worker:

1. **`MIGRATION_EXECUTE_ENABLED`** (default **False**) — the operator's explicit
   acknowledgement that a restorable backup exists (issue #7). Closed → `403`.
2. **Omission acknowledgement** — `acknowledge_omissions=true` is required. The
   report is the product; executing without reading it is refused → `422`.
3. **Confirmation** — when `MIGRATION_EXECUTE_REQUIRE_CONFIRMATION` is set
   (default True), batch `confirmation` must equal
   `MIGRATE <number of selected databases> DATABASES` → `422`. The single-DB
   route still requires its target database name.

The HTTP process performs no source enumeration, DDL, export, or import during
execution. `nova-worker` claims a durable job from `NOVA_SYSTEM`, resolves the
originating session from Redis, and runs under that user's active role. The job
record contains request options and progress, never a password or encrypted
password. If the session disappears or its security context changes before
claim, execution fails closed.

### Preflight (fail fast)

`POST /api/v1/migration/preflight` reads the caller's own grants (``SHOW GRANTS``
on the target, as the caller) and, for data movement, reports whether a transfer
stage is configured. It is **pure analysis over a read** — nothing is created.

- Only privileges relevant to the plan are required (a tables-only plan does not
  demand `CREATE VIEW`/`CREATE FUNCTION`).
- Scope is respected: `ALL DATABASES` / `ALL TABLES IN ALL DATABASES` cover any
  database; `DATABASE <name>` covers only that one; `CREATE DATABASE` needs a
  catalog-scope grant.
- **Execute runs the preflight first** (``MIGRATION_EXECUTE_PREFLIGHT``, default
  on) against the exact plan it is about to run, and refuses with **409** rather
  than half-applying. The response names the missing privilege and why it is
  needed.

### Task reconstruction

StarRocks has no `SHOW CREATE TASK`. `information_schema.tasks` exposes
`SCHEDULE` and `DEFINITION` (its filter column is `DATABASE`, not
`DATABASE_NAME`), so `verdicts.reconstruct_task_ddl` rebuilds a `CREATE TASK`.
It is a first-class plan step (after functions), retargeted to the target
database, and stays **lossy** — properties and the exact original statement may
differ. A task without a schedule or body yields no definition rather than an
invalid statement. PIPEs cannot be reconstructed: `information_schema.pipes` has
no SELECT body.

### Data movement (11-C)

`include_data=true` copies rows after the schema is applied. The move is
storage-agnostic and reuses the `@stage` mechanism:

1. **Export** on the source: `INSERT INTO FILES('path'='s3://<bucket>/migration-staging/<run>/<db>/<table>/', 'format'='parquet', ...) SELECT <cols> FROM src.t`.
2. **Import** on the target: `INSERT INTO target.t SELECT <cols> FROM FILES('path'='.../*.parquet', ...)`.
3. **Verify** on both sides: row count plus an order-independent
   `SUM(CAST(numeric AS DOUBLE))` digest. A mismatch is reported, not hidden.

Both clusters must reach the same object storage. The FILES() parameter set
mirrors the `@stage` builder: localhost endpoints are rewritten to the Docker
service name. Clusters on separate Docker networks need an endpoint reachable
from both engines. Path-style access is required for MinIO/S3-compatible
storage. Each table yields a `data` result (rows exported/imported, verified,
digest match, errors); `rows_moved` counts only verified copies. The copy is
per-table (not chunked) in this version.

Behaviour:

- **Runs as the caller**, through the shared `query_service` pipeline. StarRocks
  RBAC is the real authority: a caller without `CREATE DATABASE` / `CREATE TABLE`
  / … on the target is refused by preflight, not given a privileged bypass. Note that
  `GRANT ALL ON *.*` does **not** include `CREATE DATABASE`; a migration operator
  needs the explicit grants (`GRANT CREATE DATABASE ON CATALOG default_catalog`,
  `GRANT CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW ON ALL DATABASES`).
- **Per-object results** — each step reports `ok` / `failed` with the engine
  error message. A failing step does not abort the rest, so a partial run is
  fully visible.
- **Repeatable schema steps** — supported object DDL carries `IF NOT EXISTS`;
  `CREATE TASK` has no such form on StarRocks 4.1.4 and needs review before a
  re-run.
- **Audited** — queue, execute, and terminal job outcomes write
  `NOVA_SYSTEM.AUDIT_LOG` rows.
- **Nothing is retargeted at execute time from client input** — the plan is
  recomputed from the source, so a stale client plan cannot be applied.

## Module layout

```
backend/app/modules/migration/
├── __init__.py
├── schemas.py      # API contract (verdicts, requests/responses)
├── verdicts.py     # Pure classification rules + function reconstruction (no I/O)
├── retarget.py     # Pure DDL retargeting for the target (no I/O)
├── planner.py      # Pure dependency-ordered apply planning (no I/O)
├── preflight.py    # Pure grant parsing/analysis (no I/O)
├── data_mover.py   # Pure data-copy SQL builders + copy plan (no I/O)
├── source.py       # Resolve a registered source into a real connection
├── engine.py       # starrocks-cluster-sync adapter (status only)
├── repository.py   # Source metadata reads + NOVA_SYSTEM registry
├── jobs.py         # Durable job state + short-lived session link
├── job_worker.py   # Job claim and worker-side operation dispatch
├── service.py      # Orchestration + credential filtering + audit
└── router.py       # HTTP submission, read responses, and job status
```

Tests: `backend/tests/unit/test_migration_connector.py`,
`backend/tests/unit/test_migration_retarget.py`,
`backend/tests/unit/test_migration_data_mover.py`, and
`backend/tests/unit/test_migration_preflight.py` (unit, no engine);
`backend/tests/integration/test_migration_l3.py` (real StarRocks 4.1.4),
including execute end-to-end, idempotency, and data movement with count/digest
verification. The existing L3 suite registers the same test engine as source
and target; it does not by itself prove an independent source cluster can
reach shared storage or the target. Report any skipped data-copy test as a
coverage gap, not as a pass.

## Limitations

- Sync materialized views are not listed by
  `information_schema.materialized_views` on 4.1.4; if the engine reports one it
  is classified `lossy`, but the connector does not synthesise entries the
  engine does not expose.
- Policy enumeration is best-effort: `SHOW MASKING POLICIES` /
  `SHOW ROW ACCESS POLICIES` may be absent on some builds, in which case the
  report is empty rather than wrong.
- Data movement needs object storage that both clusters can reach.
- `SHOW CREATE FUNCTION` does not exist on 4.1.4. SQL UDFs are reconstructed from
  `SHOW FULL FUNCTIONS`; argument names are inferred from the body's backticked
  identifiers, which is why the verdict is `lossy`, not `migratable`.
- Execute is implemented but **disabled by default**; the operator opens the gate
  after a restorable backup exists (#7). The flag is the operator's
  acknowledgement, not a guarantee Nova can enforce.
- Batch requests allow 1–50 unique databases. A valid originating session
  must remain available until the worker claims the job; logout, expiry, or
  security-context change makes the job fail closed.
- The copy is per-table and not chunked: a large table is one export and one
  import, with no retry or parallelism yet.
- Pipe definitions and non-native UDF bodies are still `blocked`, so a cutover
  omits them by design — visible in the plan's blocked list.
- Task reconstruction is best-effort and `lossy`; some engine builds disable
  tasks entirely, in which case none are enumerated.
