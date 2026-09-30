# SQL Frontend Migration Report

The production path now uses the central ANTLR frontend, typed statements,
semantic effects, registered planners, one ordered rule pass and a central
executor. StarRocks continues physical planning and distributed execution.

```text
HTTP / MySQL proxy
  → normalization + hard guard
  → ANTLR → typed AST → semantic analysis / lazy binding
  → registered planner + capabilities → ordered rules
  → credential-free execution plan → central executor
      → late stage preparation → StarRocks SQL
      → existing Nova service
      → ordered composite steps, stopping on failure
  → redacted result + audit
```

## Feature Status

| Feature | Migrated behavior |
| --- | --- |
| Native SELECT, CTE, DML, DDL, CTAS, SHOW, EXPLAIN | Strict central parsing; unchanged normalized engine SQL; no eager binding/version lookup |
| Stages | Central reference nodes; shared authorized resolution and CSV preparation; per-reference descriptors and parameters; existing translator/injector |
| Stage browse/load/export | Grammar forms and central classification; existing lowering and export guard preserved |
| CREATE TASK | Central context lowering; metadata service, timezone and role checks; rejects credential persistence |
| CREATE ML_MODEL, ML_PREDICT, ML_PREDICT_TABLE, ML_FORECAST | Central contexts/tokens and registered service actions; prior validation, columns, warnings and bounded inference retained |
| Password-change policy | Typed action to user-flag service; no raw engine submission |
| Ranger security | Grammar-derived managed operations; guard before dispatch; unsupported managed grants rejected; native mode retained |
| ML streaming/training preparation | Shared frontend and stage execution helper |
| Assistant syntax validation | Central frontend with pure validation; no catalog or execution |
| Proxy sessions/variables | Existing session handlers; one stage classification tree per source, reused when unchanged |
| Binder/capabilities | Lazy authorized catalog protocol, per-request caches, explicit conservative 4.1.4 profile |
| Extensibility | Dummy AST/planner binds a column and produces capability-dependent composite SQL through the unchanged service flow |

## Important Files

- [Parser and source handling](../../backend/app/sql_frontend/parser.py)
- [Typed AST and builder registry](../../backend/app/sql_frontend/ast/builder.py)
- [Analysis and effects](../../backend/app/sql_frontend/analysis/analyzer.py)
- [Plans and typed action payloads](../../backend/app/sql_frontend/planning/execution.py)
- [Planner and rule registries](../../backend/app/sql_frontend/planning/planner.py)
- [Lazy StarRocks binding](../../backend/app/sql_frontend/binding/starrocks.py)
- [Capability profiles](../../backend/app/sql_frontend/capabilities/starrocks.py)
- [Central executor](../../backend/app/sql_frontend/execution/executor.py)
- [Existing-service adapters](../../backend/app/sql_frontend/execution/adapters.py)
- [Shared stage execution](../../backend/app/sql_frontend/execution/stages.py)
- [Query lifecycle integration](../../backend/app/modules/query/service.py)
- [Architecture and extension workflow](../arch-13-sql-frontend.md)

The generated grammar diff is large because new rules/tokens renumber ANTLR
artifacts. The upstream grammar pin is unchanged; all grammar edits are inside
marked Nova regions.

## Validation

Final gate results are recorded below. Focused runs overlap
with the full unit run and must not be added to its count.

| Gate | Result |
| --- | --- |
| Dedicated frontend tests | 91 passed; zero failures or skips; included in full suite |
| Full unit suite | 4,584 passed; zero failures or skips; 3 warnings |
| Assistant behavior evals | 48/48 scenarios, 165/165 checks; zero failures |
| Live selected regressions | 70 passed, zero failed, 1 skipped |
| Changed-file Ruff | Passed for all changed Python files, excluding generated artifacts by repository configuration |
| Grammar drift | Passed for both grammar files at pinned commit `4a9848edf03f5c936dac664b2d52527f48e72eb0` |
| Regeneration consistency | Eight grammar/source artifacts byte-identical after a second pinned generation |
| Mypy, report-only | 241 errors outside generated grammar; zero diagnostics in `app/sql_frontend/`. Original snapshot had 246 non-generated errors. |

The live selection includes the real HTTP router/auth/session path and a real
MySQL CLI container against the real Nova proxy. It covers native statement
forms, session variables and keyword literals, stage execution and redacted
audits, task metadata, streaming, engine 4.1.4 regressions and stage
browse/load/export. New objects use unique names and `finally` cleanup.

The one live skip is the separate Ranger regression. It requires an isolated
patched StarRocks/Ranger backend at `NOVA_FRONTEND_RANGER_URL` and a security-admin
session in `NOVA_FRONTEND_RANGER_TOKEN`. The ordinary isolated Compose stack does
not provide that patched fixture. Managed decoding, native-mode parity, strict
unsupported-operation rejection and guard ordering are covered by offline tests;
this does not substitute for the skipped Ranger end-to-end check.

The live stack used Compose project `nova-sql-frontend`, FE MySQL port `39030`,
FE HTTP `38030`, Arrow `39408`, storage API `39000`, storage console `39001` and
Redis `36379`. Only that stack was seeded and it was removed after validation.
The development FE and BE remained healthy.

### Overhead

Run `cd backend && uv run python -m tests.benchmark.sql_frontend_report`.
The report checks exact native SQL preservation on every iteration and compares
the legacy native compatibility parser with the new parse/build/plan path.
After five warm-up rounds, it measures 300 samples on six statements per path.

| Warm CPU path | Median | P95 |
| --- | --- | --- |
| Legacy native compatibility parser | 0.94 µs | 262.96 µs |
| Central parse, AST and plan | 295.56 µs | 691.92 µs |

Median added CPU time was 294.62 µs on this host. This measurement excludes
engine I/O, audit, secrets, binding and optional capability detection. It is a
local comparison, not a latency SLA or a timing assertion. Correctness tests
separately prove one parse for unchanged statements, zero native binder calls,
cached explicit binding and no native rewriting.

## Remaining Debt and Boundaries

The repository's mypy baseline remains unresolved outside this migration.
The isolated patched Ranger live check remains unverified until its fixture is
provided. Existing semantic-expression and documented compatibility entrypoints
remain for their callers; production query routing no longer uses them as
feature detectors.

Original and normalized source are preserved separately; spans refer to the
normalized source and do not provide a source map for removed UI schema
placeholders. Metadata unavailable from the engine remains absent rather than
invented. Binder cache lifetime is a request, so it cannot stale across requests.

Composite plans do not provide transactions or automatic rollback. No MERGE,
ALL BY NAME, schema evolution, physical optimizer or public EXPLAIN NOVA feature
was implemented. MERGE readiness consists of tested binding, capabilities,
registry/rule registration and composite lowering interfaces; all three MERGE
flags are disabled in the pinned profile.
