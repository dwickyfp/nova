# Control-plane baseline

Status: WP-00 in progress. This is an inventory and evidence checkpoint, not
permission to cut over or a completed metadata migration.

Source baseline: `c6a51ff2e5a94a46191884ded40fc15394de27ed`.
The attached architecture spec was based on an older source snapshot.

## Collected evidence

`inventory.json` records read-only SHOW/SELECT discovery on the existing
development StarRocks service, including schema columns, key models, DDL hashes,
row counts and observation timestamps. It contains no table row payloads or
credentials. The application was running during collection: these counts are
not a transactionally consistent export and must never authorize cutover.

- 157 live NOVA_SYSTEM tables: 150 Primary Key, seven Duplicate Key tables.
- Three live agent tables have no current application reference:
  CONFIG_AGENT_CAPABILITIES (zero rows), CONFIG_AGENT_MESSAGES (117 rows), and
  CONFIG_AGENT_SESSION_EVENTS (4,910 rows). Removal commit `ea845165` explains the
  latter histories. Preserve these records in migration, with no ordinary app
  write grants; do not discard them as scanner noise.
- Current Streams add CONFIG_STREAM_SOURCES, CONFIG_STREAM_DEFINITIONS and
  AUDIT_STREAM_SOURCE_COMMITS, absent from the supplied CSV. Source journal and
  lineage tables live beside user data so they can commit in the engine
  transaction; they are not a second Nova control-plane database.
- Dynamic f-string DDL resolves CONFIG_DATAVIZ_PUBLICATIONS and
  CONFIG_SEMANTIC_BI_DATASETS. ML `*_PK` names are migration helpers, not three
  additional live stores. Prefixes and docstring words are not table names.
- The source scanner records 740 direct system API calls, including two `self`
  calls in the connection factory, plus 51 indirect candidates reached through
  aliases, injected defaults or pass-through adapters. The expanded scanner also
  records raw connection factories and metadata-bearing cursor functions, adding
  84 candidates. It inventories auxiliary scripts/tests.
  Review status is explicit; locating a call does not prove its SQL semantics.

The current Docker project is `docker`, using the engine and development
Compose files. Its original 14 services include patched FE/BE, Ranger, Superset,
object storage, critical/cache-shared Redis and OpenSandbox. Docker reports
eight CPUs and about 8 GiB RAM on OrbStack/aarch64. No duplicate stack was made.
The project now also includes the private PG17 fixture described in
`QA_FOUNDATION.md`, bringing the running project to 15 services.

## Process and transaction boundaries

The API lifespan owns schema bootstrap, LLM UDF registration, ML sweeping,
catalog/news/search work and buffered usage flushers. Its schema lock failed
open at baseline; this branch now rejects lock failure. The general worker starts
Tasks, migration, ML, automation, notebook and autopilot loops. Scheduler
leadership/transport uses Redis, while records live
in StarRocks. These must move to the approved explicit workload owners.

Preserve the existing SQL frontend and shared agent loop. Files and notebook
revisions span object storage and metadata; Dataviz spans Superset and Nova;
Ranger writes require FE propagation proof; Stream consumption requires engine
commit evidence before cursor publication. PG transactions alone cannot make
these external effects atomic.

## Remaining inventory gates

METADATA_OWNERSHIP.csv covers each live/source candidate with a proposed owner
and destination. Its REVIEW_REQUIRED fields are unresolved work, not defaults
that a migrator may accept. Complete the table-level mapping of the classified
calls, transaction boundaries, logical uniqueness,
foreign-key/orphan policy, original timestamp provenance and consumer privileges.
Duplicate Key history needs a lossless row-identity strategy before import.

`WAVE_A_REVIEW.md` records the inspected preference/workspace/stage/audit paths;
`WAVE_B_REVIEW.md` covers the Task repository and Stream control receipts;
`WAVE_C_REVIEW.md` separates Entity/Feature/Search metadata from engine payloads;
`WAVE_D_REVIEW.md` covers Assistant, Studio and Notebook repositories.
`WAVE_E_REVIEW.md` covers Intelligence revision stores,
ML/migration jobs, Autopilot and the monitoring split. `WAVE_F_REVIEW.md` covers
Studio workflows, knowledge, sharing and history. `WAVE_G_REVIEW.md` covers
Catalog, Explorer and query metadata/engine boundaries.
The 791 pooled/indirect calls and 84 raw connection/cursor candidates now have
source-reviewed destination classifications (875 total). This is a static path
classification, not a completed ownership/transform or authorization gate.
`WAVE_H_REVIEW.md` covers governance, publication and bootstrap;
`WAVE_I_REVIEW.md` covers the additional direct connections and cursor writers.
`CONSTRAINT_PROFILE.json` contains read-only aggregate checks, including two
duplicate workspace revision groups that must not be silently deduplicated.
The expanded 44-check profile also found 135 Task node rows without a parent
graph run. Other checked provider/model references, alias defaults, message
sequence/ownership, agent event references, ML/Semantic/Feature active versions,
Notebook runtime scope and Dataviz idempotency had zero observed violations.
These checks do not cover every FK, JSON reference, timestamp or import constraint.
It also records the distinction between a personal principal and a shared
workspace namespace, and the pending session-revocation defect.

FEATURE_QA.csv now includes 67 docs pages, 55 UI route declarations and 619 live
OpenAPI operations. ENTRYPOINTS.csv records current workload owners and proposed
process destinations. Extend acceptance cases with SQL/proxy and background
behavior beyond endpoint declarations. NOT_RUN means no
new-refactor evidence exists; previous release results do not certify this one.

Reproduce collection from backend with an explicitly selected existing engine:

```sh
uv run python scripts/inventory_control_plane.py \
  --output ../docs/architecture/production-refactor \
  --live-host 127.0.0.1 --live-port 29030 --live-api http://127.0.0.1:8000
```

The script never initializes schemas, starts containers, exports row values or
prints passwords. Preserve accumulated QA results when refreshing inventory.

## Table binding follow-up

`TABLE_ACCESS_REVIEW.csv` binds 755 current metadata/schema/adapter sites to
reviewed table sets and source hashes. Four earlier Explorer classifications
were corrected to engine observations. All current metadata/mixed-adapter sites
have a binding; target grants and transform/constraint decisions remain open.
`WAVE_J_REVIEW.md` describes the binding modes and seven bootstrap-only live
tables. `DEPENDENCIES.md` records current process/store dependencies and the
cross-system transactions that the PG cutover must replace.
