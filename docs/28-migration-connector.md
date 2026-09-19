# Module 28: Migration Connector (Phase 11 v1 — Assessment + Dry-run + Plan)

> Connect to a source StarRocks cluster, enumerate its objects, preview a
> per-object dry-run verdict, and build a dependency-ordered apply plan —
> without executing any cutover.

---

## Status

**v1 — Assessment + Dry-run + Plan. Implemented.** Execute is **not** implemented
and must not be: cutover is gated on issue **#7 (backup/restore)**. There is no
Execute endpoint and no execution path behind any flag. The **plan** endpoint is
read-only: it retargets definitions for the target database and returns the exact
statements an execute path would run, but executes nothing.

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
| Execute / cutover | ❌ gated on #7 |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/migration/capabilities` | Declares implemented phases; `execute_available: false` |
| GET | `/api/v1/migration/engine` | Binary availability (no execution) |
| GET | `/api/v1/migration/sources` | List registered source clusters |
| POST | `/api/v1/migration/sources` | Register a source cluster by address |
| POST | `/api/v1/migration/enumerate` | Enumerate a database's objects on a **registered source** |
| POST | `/api/v1/migration/dry-run` | Classify objects on a **registered source**; read-only |
| POST | `/api/v1/migration/plan` | Build a dependency-ordered apply plan; read-only |
| POST | `/api/v1/migration/preflight` | Check target privileges and shared storage; read-only |
| POST | `/api/v1/migration/execute` | Apply the plan to the target; **gated on #7**, off by default |

`source` is required on enumerate and dry-run. If it is missing the request is
rejected (`422`) and if it is unknown the request returns `404`; the local engine
is never used as a silent fallback.

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
| Function (native SQL) | `SHOW FULL FUNCTIONS` | `migratable` |
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

- **A source is addressed by host/port/username plus a secret *reference*.**
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

`backend/app/modules/migration/engine.py` is a thin interface plus a stub. It
only performs a filesystem status check; no method executes the binary, uses
`subprocess`, or shells out.

The binary's license is **not declared** (no `LICENSE`, no pom `<licenses>`, no
public source repo → all-rights-reserved by default). v1 therefore **adopts and
invokes, never bundles or redistributes**. The operator installs the official
binary and points `MIGRATION_CLUSTER_SYNC_BINARY` at it. A missing binary is
reported as a typed, non-fatal condition; assessment and dry-run still work.

## Lossy / skipped list (v1)

This is the report the operator must review before any future cutover:

- **Skipped (cannot migrate):** `MASKING POLICY`, `ROW ACCESS POLICY`.
- **Lossy (migrates with loss):** `TASK` (no definition collected), `PIPE`
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
function may be called from a body. Objects with no usable definition (tasks,
pipes, policies, non-native UDFs) are reported as **blocked** with a reason —
never silently dropped.

The plan is surfaced by `POST /api/v1/migration/plan`. The apply path is
`POST /api/v1/migration/execute`, described below.

## Execute (gated on #7)

`POST /api/v1/migration/execute` applies the plan to the target. Three gates are
enforced in the service, so the router stays thin:

1. **`MIGRATION_EXECUTE_ENABLED`** (default **False**) — the operator's explicit
   acknowledgement that a restorable backup exists (issue #7). Closed → `403`.
2. **Omission acknowledgement** — `acknowledge_omissions=true` is required. The
   report is the product; executing without reading it is refused → `422`.
3. **Target confirmation** — when `MIGRATION_EXECUTE_REQUIRE_CONFIRMATION` is set
   (default True), `confirmation` must equal the target database name → `422`.

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
mirrors the `@stage` builder: the host-side endpoint is rewritten to the
Docker service name, and path-style access is required for MinIO/S3-compatible
storage. Each table yields a `data` result (rows exported/imported, verified,
digest match, errors); `rows_moved` counts only verified copies. The copy is
per-table (not chunked) in this version.

Behaviour:

- **Runs as the caller**, through the shared `query_service` pipeline. StarRocks
  RBAC is the real authority: a caller without `CREATE DATABASE` / `CREATE TABLE`
  / … on the target gets a per-object denial, not a privileged bypass. Note that
  `GRANT ALL ON *.*` does **not** include `CREATE DATABASE`; a migration operator
  needs the explicit grants (`GRANT CREATE DATABASE ON CATALOG default_catalog`,
  `GRANT CREATE TABLE, CREATE VIEW, CREATE MATERIALIZED VIEW ON ALL DATABASES`).
- **Per-object results** — each step reports `ok` / `failed` with the engine
  error message. A failing step does not abort the rest, so a partial run is
  fully visible.
- **Idempotent** — every object step carries `IF NOT EXISTS`; a re-run creates
  nothing new and does not fail.
- **Audited** — one `NOVA_SYSTEM.AUDIT_LOG` row (`action="execute"`) with the
  success/failure status.
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
├── service.py      # Orchestration + credential filtering + audit
└── router.py       # HTTP surface (no Execute)
```

Tests: `backend/tests/unit/test_migration_connector.py`,
`backend/tests/unit/test_migration_retarget.py`,
`backend/tests/unit/test_migration_data_mover.py`, and
`backend/tests/unit/test_migration_preflight.py` (unit, no engine);
`backend/tests/integration/test_migration_l3.py` (real StarRocks 4.1.4),
including execute end-to-end, idempotency, and data movement with count/digest
verification.

## Limitations

- Sync materialized views are not listed by
  `information_schema.materialized_views` on 4.1.4; if the engine reports one it
  is classified `lossy`, but the connector does not synthesise entries the
  engine does not expose.
- Policy enumeration is best-effort: `SHOW MASKING POLICIES` /
  `SHOW ROW ACCESS POLICIES` may be absent on some builds, in which case the
  report is empty rather than wrong.
- Data movement (`INSERT INTO FILES()`) is designed for a later phase; v1 does
  not move data.
- `SHOW CREATE FUNCTION` does not exist on 4.1.4. SQL UDFs are reconstructed from
  `SHOW FULL FUNCTIONS`; argument names are inferred from the body's backticked
  identifiers, which is why the verdict is `lossy`, not `migratable`.
- Execute is implemented but **disabled by default**; the operator opens the gate
  after a restorable backup exists (#7). The flag is the operator's
  acknowledgement, not a guarantee Nova can enforce.
- Data movement requires the source and target to reach the same object storage;
  the transfer runs through a stage, not a direct cluster-to-cluster link.
- The copy is per-table and not chunked: a large table is one export and one
  import, with no retry or parallelism yet.
- Pipe definitions and non-native UDF bodies are still `blocked`, so a cutover
  omits them by design — visible in the plan's blocked list.
- Task reconstruction is best-effort and `lossy`; some engine builds disable
  tasks entirely, in which case none are enumerated.
