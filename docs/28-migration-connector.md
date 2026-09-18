# Module 28: Migration Connector

> Easy, UI-driven migration from a native StarRocks deployment to Nova — v1
> Assessment + Dry-run. No object is moved yet; the wizard tells you exactly what
> will move, what will move partially, and what will not move at all.

> **Filename note:** NOVA-85's spec named this `docs/11-migration-connector.md`,
> but `docs/11-user-access-control.md` already occupies that number. This doc is
> the migration connector, placed at the next free number (28).

---

## Concept

Phase 11 — Migration Connector is an orchestrator over surfaces Nova already has,
not a new engine. The user connects to a source StarRocks deployment, the wizard
enumerates its objects, and a dry-run reports a verdict for each one:

| Verdict | Meaning |
|---|---|
| `migratable` | Carries over as-is through an existing Nova surface. |
| `lossy` | Carries over partially; the `reason` names what is dropped. |
| `skipped` | Cannot carry over at all; the `reason` says why and what to recreate. |

The contract is **lossy-by-explicit-report**: a declared omission is the
product, a silent omission is a defect. The dry-run never says "done" over an
object it cannot move.

### v1 scope

- **In:** connect, enumerate (database/table/view/materialized view/task/pipe/
  function), and dry-run verdicts.
- **Out (hard boundary):** any cutover or execute path, behind any flag. Cutover
  is a separate issue gated on roadmap **#7 (backup/restore)**.

---

## Verdict map

Derived from the NOVA-84 research (`nova-84-migration-surfaces.md`, engine pin
`4a9848ed…` / tag 4.1.4).

| Native object | Verdict | Why |
|---|---|---|
| Database | `migratable` | Created on the target. |
| Table | `migratable` | Full DDL via `SHOW CREATE TABLE`. |
| View | `migratable` | Full DDL via `SHOW CREATE VIEW`. |
| Async materialized view | `migratable` | `SHOW CREATE MATERIALIZED VIEW` emits `REFRESH`/`PARTITION BY`/`PROPERTIES`. |
| Sync materialized view | `lossy` | No surface emits the sync MV's refresh/partition/properties. |
| TASK | `lossy` | No `SHOW CREATE`; only a partial `information_schema.tasks` projection. |
| PIPE | `lossy` | No `SHOW CREATE`; DDL reconstructed, `SELECT` body may be incomplete. |
| FUNCTION | `lossy` | No `SHOW CREATE`; non-native (Java/Python) UDF artifacts are not carried. |
| MASKING POLICY | `skipped` | No DDL export surface at all; must be recreated. |
| ROW ACCESS POLICY | `skipped` | No DDL export surface at all; must be recreated. |

### Materialized-view surface

Materialized views are enumerated from `information_schema.materialized_views`,
**never** `information_schema.tables`: the latter cannot distinguish an MV from
a view, and a view exported through the MV surface (or vice versa) loses refresh
and partition state. The enumerate response names the surface it used in
`materialized_view_source` so a regression is visible in the payload itself.

---

## Operations

### Engine status

```
GET /api/v1/migration/engine
```

Reports whether the operator-provided `starrocks-cluster-sync` binary is
available at `NOVA_MIGRATION_ENGINE_PATH`. Nova never bundles, mirrors, or
redistributes the tool (see *Security*). An absent binary is reported, **not**
worked around, and never triggers a download.

### Connect

```
POST /api/v1/migration/connections
{
  "host": "source.internal", "port": 9030,
  "username": "sync_user", "password": "..."
}
→ { "connection_id": "…", "connected": true, "server_version": "4.1.4", … }
```

The password is encrypted server-side into an ephemeral, TTL'd store keyed by
the authenticated session, and is never returned. The caller keeps
`connection_id` — an opaque handle, not the secret.

### Enumerate

```
POST /api/v1/migration/connections/{connection_id}/enumerate?database=analytics
```

Returns every enumerated object of `database` with its kind.

### Dry-run

```
POST /api/v1/migration/connections/{connection_id}/dry-run
{ "database": "analytics" }        # omit to assess every user database
```

Returns one row per object plus a summary (`migratable`/`lossy`/`skipped`/`total`)
and the `has_lossy` / `has_skipped` flags a UI gates its acknowledgement on.

---

## Security

**Credential-invisible (NOVA-85 invariant 1).** A source password never appears
in an API response, a log, an audit row, a reason string, `NOVA_SYSTEM`, or
frontend state. It is encrypted at rest with the session Fernet key and has a
one-hour TTL. Tests plant sentinel credentials in the connect body and assert
they never come back out.

**Engine redistribution (NOVA-84 Ruling A).** `starrocks-cluster-sync` declares
no license — no `LICENSE`/`NOTICE`, no `<licenses>` in its pom, no license
headers, no public source repo — and its fat JAR bundles Oracle MySQL
Connector/J 8.0 (GPLv2 + Universal FOSS Exception). Nova therefore **adopts and
invokes** an operator-provided binary; it does not bundle, vendor, mirror, or
redistribute it. The wizard fails with a typed error naming the configured path
when the binary is absent, and never auto-downloads.

**MV sensitive-property filter (NOVA-84 Ruling B, hardening).** The engine's
MV DDL branch does not apply its `hidePassword` flag. MV properties are
operational today (refresh/TTL/warehouse name) and no secret leaks, but any
consumed MV DDL must still be routed through the existing
`sql_guard.redact_sql_credentials` and credential-named property keys dropped
via `drop_sensitive_properties` — one redactor, not two.

**RBAC.** `ACCOUNTADMIN` is immutable and is never replicated or offered for
migration.

---

## Implementation notes

| Concern | Location |
|---|---|
| Schemas / verdict vocabulary | `backend/app/modules/migration/schemas.py` |
| Source metadata enumeration | `backend/app/modules/migration/repository.py` |
| Assessment + dry-run logic | `backend/app/modules/migration/service.py` |
| Engine adapter (no execute) | `backend/app/modules/migration/engine.py` |
| Ephemeral source credential store | `backend/app/modules/migration/source_store.py` |
| HTTP surface | `backend/app/modules/migration/router.py` |

The engine adapter has no `execute` method at all — a stronger guarantee than a
flag, because a caller cannot reach an execution path by flipping a boolean. The
unit suite asserts the method is absent and no route matches `execute`/`cutover`.

---

## Limitations

- **No cutover.** Execute is out of scope for v1 and gated on #7 (backup/restore).
- **Masking/row-access policies** are reported as `skipped` rows only when the
  source exposes their `information_schema` views; a source that hides them still
  cannot carry them, and the dry-run's category-level finding stands.
- **L3 on external catalogs** (MV-over-external-catalog properties,
  `getOriginalViewDefineSql()` secret potential, runtime-image throughput) is
  recorded as open and non-blocking in NOVA-84; it belongs to a later L3 pass if
  external catalogs are exercised.
- The source engine is assumed to be a StarRocks deployment reachable over the
  MySQL protocol on the given host/port.
