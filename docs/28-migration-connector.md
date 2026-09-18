# Module 28: Migration Connector (Phase 11 v1 — Assessment + Dry-run)

> Connect to a source StarRocks cluster, enumerate its objects, and preview a
> per-object dry-run verdict — without executing any cutover.

---

## Status

**v1 — Assessment + Dry-run. Implemented.** Execute is **not** implemented and
must not be: cutover is gated on issue **#7 (backup/restore)**. There is no
Execute endpoint and no execution path behind any flag.

| Capability | v1 |
|---|---|
| Register a **source cluster** (host / port / username + secret reference) | ✅ |
| Enumerate databases / tables / views / MVs / functions / tasks / pipes / policies **on that source** | ✅ |
| Dry-run verdict per object (`migratable` / `lossy` / `skipped`) with reason | ✅ |
| Audit rows for source registration and dry-run | ✅ |
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
- **Lossy (migrates with loss):** `TASK` (partial reconstruction), `PIPE`
  (`SELECT` body lost), sync materialized views (no `REFRESH`/`PROPERTIES`),
  non-native UDF bodies (jar/payload not carried).
- **Not migrated by this connector at all:** RBAC users/roles/grants (passwords
  are never exportable), `ACCOUNTADMIN` (**must not** be replicated), external
  catalog credentials, resource groups, storage volumes, session variables.

## Module layout

```
backend/app/modules/migration/
├── __init__.py
├── schemas.py      # API contract (verdicts, requests/responses)
├── verdicts.py     # Pure classification rules (no I/O)
├── source.py       # Resolve a registered source into a real connection
├── engine.py       # starrocks-cluster-sync adapter (status only)
├── repository.py   # Source metadata reads + NOVA_SYSTEM registry
├── service.py      # Orchestration + credential filtering + audit
└── router.py       # HTTP surface (no Execute)
```

Tests: `backend/tests/unit/test_migration_connector.py` (unit, no engine) and
`backend/tests/integration/test_migration_l3.py` (real StarRocks 4.1.4).

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
