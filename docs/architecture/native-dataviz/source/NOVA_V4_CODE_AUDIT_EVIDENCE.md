# NOVA MAIN (4) — STATIC CODE AUDIT EVIDENCE
**Scope:** focused native Chart/Dashboard, Studio, Viz Core, metadata and Superset references. **Evidence classification:** source inspected; no runtime tests executed. **Date:** 2026-11-13.

## 1. Architecture status that affects design

1. The repository now records an active PostgreSQL schema-0045 metadata epoch for its dev runtime; `docs/architecture/production-refactor/DELIVERY_STATUS_AND_QA_BACKLOG.md:21-35` explicitly warns that whole production refactor/HA QA is unfinished. New Dataviz metadata should use existing `backend/app/core/metadata_db.py` and metadata migrations, not StarRocks or an independent PG pool.
2. Backend UI/chart code still relies on Superset in a meaningful set of files. A static pattern inventory (`app.modules.superset`, `/bi/`, `bi_object_id` etc.) finds **22 backend `.py` files** and **27 frontend `.ts/.tsx` files** touching legacy conventions. Those are *pattern-matched candidates*, not a complete dependency graph. Codex must refresh and expand inventory.
3. Existing frontend dependencies include Apache ECharts (`^6.1.0`), TanStack React Table (`^8.21.3`), React 19, Monaco and Zod; no `gridstack` dependency appears in `frontend/package.json` or current `frontend/src`. Add GridStack with a pinned, OSS-verified release and validate React wrapper against deployed React.

## 2. Exact relevant source evidence

| Finding | File:line (from uploaded zip) | Observed behavior | Required target action |
|---|---|---|---|
| Studio keeps Dataviz mounted | `frontend/src/features/studio/index.tsx:73` | `datavizMounted` tracks tab and retains view lifecycle | Replace iframe content with native Dataviz route/shell; preserve stable document state, not legacy frame state |
| Old saved artifacts route | `frontend/src/features/studio/index.tsx:445` | `view === "artifacts" || view === "dashboards"` renders existing BI artifact UI | Replace navigation with unified 5-type Artifact Library; preserve legacy deep links via mapping |
| Superset frame is imported | `frontend/src/features/studio/dataviz/dataviz-workspace.tsx:9` | `BiFrame` from BI feature | Remove on validated cutover, not before |
| Dataviz agent context depends on frame | `frontend/src/features/studio/dataviz/dataviz-workspace.tsx:56` | `turnContext` extracts Superset iframe/native filter state | Replace with native dashboard ID+revision+page+typed filter context |
| Sidebar old artifact label | `frontend/src/features/studio/studio-sidebar.tsx:32-40` | `StudioView` and `Artifacts` menu for charts/dashboard | Dataviz becomes native dashboard tab; Artifact Library adds spreadsheet/whiteboard/html/chart/dashboard |
| Legacy canvas is explicitly read-only | `frontend/src/features/viz/dashboard-canvas.tsx:20` | Legacy view is a tiled zone grid | Build shared `NativeDashboardCanvas` with Edit/View and Agent/Dataviz skins |
| Canvas hardcoded 24 columns | `frontend/src/features/viz/dashboard-canvas.tsx:137` | CSS `repeat(24,...)` | GridStack canonical 12 columns with validated v1 converter |
| Current filter override keyed by field | `backend/app/modules/viz/spec.py:305` | `with_extra_filters` replaces previous predicate for same field | Scoped typed Boolean filter AST; preserve local/global conditions |
| ZoneRect 24-column constraint | `backend/app/modules/viz/spec.py:318` | `x≤23`, `w≤24`; zone validator checks non-overlap | New v2 coordinates; do not mutate v1 contract directly |
| Dashboard max 48 zones | `backend/app/modules/viz/spec.py:411` | Hard cap `max_length=48` | Explicit production page/widget quota (benchmark-backed) and pagination/lazy rendering |
| Viz rows limited | `backend/app/modules/viz/service.py:44` | `MAX_COMPUTE_ROWS=500` | Bounded per-chart aggregation + separate server-side table/pivot pagination |
| Type inference from sample | `backend/app/modules/viz/compiler.py:95`; `service.py:122-129` | Source type probes sample rows up to 100; `infer_column_types` uses non-null sample | Use engine/catalog schema and logical type metadata |
| Dashboard compute serial | `backend/app/modules/viz/service.py:171-244` | Async `for` awaits each zone query in sequence | Concurrent bounded batch execution/dedup/cancellation with per-zone errors |
| Viz source SQL composability | `backend/app/modules/viz/compiler.py:182` | Source wraps custom SQL as subquery; `split_sql_statements` used | Harden through central semantic read-only parser and parameter binding for each entrypoint |
| Database/schema scope args | `backend/app/modules/viz/service.py:131-146,261+` | `compute` receives database/schema but `_execute` uses `database=None`, `schema=None` | Explicit, typed catalog/source scoping; no name resolution ambiguity |
| Studio create directly publishes BI | `backend/app/modules/agents/studio_router.py:337+` | `create_artifact` calls Superset `publish_chart`/`publish_dashboard` | Native Document Service and revisioned chart/dashboard objects |
| Legacy dashboard compute route | `backend/app/modules/agents/studio_router.py:253+` | Client sends `DashboardSpec` and filter values for computation | New immutable persisted membership + current role validation and query plans |
| Superset pointer fields | `backend/app/modules/agents/artifact_repository.py:48+` | `bi_object_id`, `bi_dataset_id`, legacy conversion logic | Keep for migration only; new native IDs independent |
| Shared-dashboard projection exists | `backend/app/modules/agents/artifact_repository.py:331+` | Uses `agent_metadata.shared_dashboard` and typed spec projection | Reuse safe projection pattern; expand resource ACL user/role |
| Current PG artifact table exists | `backend/metadata_migrations/0021_agents.sql:312-331` | `nova_meta.config_studio_artifacts`, metadata revision, old BI pointers | Add typed normalized native viz schema via additive migration |
| Artifact RLS owner-centric | `backend/metadata_migrations/0021_agents.sql:710-718` | `agent_is_principal(owner_name)` policy | Add scoped role/user permissions and tests; source data remains protected by Ranger |
| Nova design primary is not sample blue | `DESIGN.md:13-17` | Semantic color tokens, primary currently brick-red, no parallel palette | Use existing `theme.css` and validated tokens; mockups are composition only |

## 3. Existing positive assets

- Typed `WorksheetSpec`, `DashboardSpec`, calculated field analyzer/compiler, ShowMe choice and presentation model in `backend/app/modules/viz/`. Build on these and add v2 schema converter.
- Caller-impersonated `QueryService`, patched Ranger roles, `SecurityContext`, explicit metadata actor transactions, PG RLS/CAS and production refactor job/outbox infrastructure.
- ECharts renderer and TanStack Table already in frontend dependencies; no AG Grid necessary.
- Agent tool catalog, Neva harness and existing eval tests: `backend/tests/eval/test_chart_data_provenance.py`, `test_dashboard_build_trajectory.py`, `test_dataviz_trajectory.py`.
- Documentation rules, strict theme tokens, lint/build/test gates and the existing design system.

## 4. What this audit **does not prove**

- This is not a live StarRocks/Ranger behavior test or end-to-end Databricks comparison.
- PG development migration completion is not proof of production failover/backup/restore readiness.
- Existing chart source read-only validation does not prove every new filter/parameter/editing path will be safe; test the new combined path.
- Repo-wide reference counts shown above are grep candidates, not complete migration inventory (dynamic imports, gateway and stored SQL may add more).
- The generated UI concept images are not implemented screenshots. They must be translated into the real Nova `DESIGN.md` system and then visually verified.


**Audited archive SHA-256:** `2b3e4edb35743171f2c06c94e150959fc7441a570a12e61b1e4793a98258e966`. This fingerprint identifies the source archive examined and is not a statement of any currently deployed commit.
