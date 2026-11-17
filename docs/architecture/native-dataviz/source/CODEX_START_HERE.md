# CODEX START HERE — NOVA NATIVE DATAVIZ

## Mission

Implement the owner-approved native Chart/Dashboard replacement for Superset using the real `nova-main (4).zip` checkout (or newer branch after verifying changed code). Build a Databricks-style **Canvas + Data** dashboard authoring workflow and true Neva collaboration, but keep the existing Nova codebase, core SQL frontend, PostgreSQL metadata authority, StarRocks+Ranger policy enforcement, and Nova `DESIGN.md`. This is implementation work, not a request to merely explain or restate architecture.

## Must-read order

1. Current repo `AGENTS.md`, `backend/AGENTS.md`, `frontend/AGENTS.md`, `DESIGN.md`, task-specific scoped guides and current `docs/architecture/production-refactor/*` status.
2. `NOVA_NATIVE_DATAVIZ_PRODUCTION_SPEC.md` — mandatory target architecture, data model, security, workflows, release gates.
3. `NOVA_NATIVE_DATAVIZ_DESIGN_SYSTEM.md` — exact UI composition, components, responsive behavior, tokens and accessibility.
4. `NOVA_NATIVE_DATAVIZ_DATA_CONTRACTS.md` — proposed schema details and typed API/filter/layout/operation contracts; reconcile against live migration chain.
5. `NOVA_DATAVIZ_INTEGRATION_MAP.md` — concrete module wiring and data flow.
6. `NOVA_V4_CODE_AUDIT_EVIDENCE.md` — verified source starting points, not claims that target has shipped.
7. `NOVA_NATIVE_DATAVIZ_QA_MATRIX.csv` — test IDs and success criteria. Report `PASS/FAIL/BLOCKED` with actual evidence, never fill PASS without execution.
8. `REFERENCE_DATAVIZ_BUILDER.png`, `REFERENCE_AGENT_COLLAB.png` — composition references only; match Nova existing tokens, not the image palette.

## Explicit product decisions

- Two **top-level** Studio tabs: Agent and Dataviz.
- **Internal** dashboard editor tabs: Canvas and Data.
- Artifacts catalog: Spreadsheet, Whiteboard, HTML Report, Chart, Dashboard. Focus here: Chart/Dashboard. Dataviz only lists dashboards.
- Same artifact IDs, document versioning, editor, query service and RBAC across Dataviz and Agent panel.
- GridStack.js (fully MIT) 12-column authoring, Apache ECharts rendering, TanStack Table + Virtual; advanced pivot aggregates in StarRocks; NO AG Grid, commercial SDK, enterprise-only required features, trial/runtime license key.
- Existing PG17 metadata owner with actor/RLS, existing SQL QueryService and StarRocks/Ranger execution, no second SQL/compiler/agent service.
- Viewer role **does not gain source rights** merely by receiving dashboard permissions.
- Native immutable publication definitions, live queries under viewer active role, distinct `view`, `interact`, `edit`, `publish`, `manage`, and `export` permission checks.
- Neva semantic API operations and humanized virtual cursor: animation is *not* proof of commit.
- Decommission Superset only after inventory/conversion and end-to-end validation; never delete unsupported assets or overwrite committed new metadata on rollback.

## Execution workstream in dependency order

1. **Baseline:** inspect branch/status/source differences; locate owners/callers; inventory Superset charts, datasets, dashboards, filter semantics, role sharing and schedules; inspect the actual PostgreSQL epoch/engine/Ranger test topology. Write a coverage/inventory report with owner, sources, compatibility and blockers. Never test destructively against non-disposable data.
2. **Contracts:** produce finalized Pydantic v2 models and frontend schemas for dataset, semantic fields, charts, dashboards, filters, ACL and operations. Show golden v1→v2 adapters before UI changes. Update design tokens only through existing `theme.css` + `DESIGN.md` and design gates.
3. **Durable resources:** implement additive PG migrations + scoped grants/RLS, versioned repositories, immutable revs, atomic CAS, idempotency receipts/outbox, owner/role/user ACL tests. Do not implement inline business SQL in routers.
4. **Data/metrics:** implement SQL/table/semantic dataset service and Data tab, read-only source parsing, bound params, field schema/provenance, local metrics, ratio/join cardinality/time calculations, StarRocks and Ranger tests.
5. **Charts:** implement reusable chart artifacts, chart builder, ECharts/TanStack rendering, query result shaping, per-chart style and preview, with real tests.
6. **Dashboard:** implement shared 12-col GridStack Canvas, widget registry, layout save/revert, view/edit/published modes, pages and filter types; test keyboard/mobile and no raw HTML persistence.
7. **Interaction & performance:** build typed filter engine with explicit scope, cascading options, cross-filter, drill, bounded query batch with cancellation/cache security and server-side pivot/table pagination.
8. **Neva collaboration:** register native tool catalog using existing harness, stable document references across chats, preview/diff/commit/undo, virtual cursor and WebSocket journal/replay. Test simultaneous user/agent edits and interruption.
9. **Sharing/exports:** implement permission matrix, current active role/Ranger checks, publish/revisions, private object-store verified exports, PDF/PNG/CSV/live-to-static report refs and native task scheduling if previously used.
10. **Superset migration:** extract/convert/verify all legacy assets. Use safe read-only copy, legacy ID mappings and explicit unsupported exceptions. Replace routes/tool effects and only then remove Superset containers/routes/dependencies in a separate final WP.
11. **Certification:** run all repository/scoped CI gates, real PG/Ranger/StarRocks, Playwright, fault/recovery, security, SLO and migration QA. Fill the QA matrix with paths/logs/evidence and release checklist; do not announce production completion with missing critical gates.

## Rules for all code changes

- Each WP must produce: implementation diff, migration notes, newly written tests, actual test commands/results, docs, risk report, and next dependencies. Keep large work staged, but never stop merely because the spec is large.
- No architectural parallelism: never create a second backend metadata pool, new SQL parser, separate auth/RLS engine, separate LLM loop, duplicate chart store, or new deployment secret scheme.
- No unsupported feature flags or production dual-writes; do not hide missing capabilities behind optimistic UI.
- Avoid adding new global CSS colors and fonts outside Nova's design system.
- No direct external user-data reads via service/admin credentials; all viewer, Neva, background and export data access follows approved identity boundaries.
- Use parameterized/typed SQL, strict input schema, least privilege, cache isolation, committed operation receipts, immutable publication, and fail-closed unauthorized outcomes.
- Never make security tests pass by changing permissions from the desired contract or disabling Ranger.
- Before editing check `git status`, active branch, concurrent changes and relevant DevMesh handoff if present; preserve unrelated changes.
- Inspect test fixture setup/teardown and Docker targets. Do not reset or destroy live development/production data. If isolated test infrastructure is unavailable, mark that gate BLOCKED and proceed with non-destructive work, providing an exact blocker.

## QA and delivery format

For every milestone report:

```text
WP: XX | Status: DONE / PARTIAL / BLOCKED
Current branch/commit:
Changed files:
Implemented behaviors:
Migration/version impact:
Auth/Ranger/secret review:
Tests executed (commands and actual PASS/FAIL/SKIP counts):
QA IDs completed (paths/evidence):
Remaining issues/blockers:
Next safe WP:
```

**Finish line:** no runtime Superset, all assets accounted for, same Chart/Dashboard engine used in Dataviz and Agent, governed metrics/filters/drill/pivot, role/user ACL, legitimate OSS, durable collaboration and exports, all required tests and restore drills verified. See §18.5 of the main spec for normative gates.

## One-shot directive you can paste into Codex

> Read `CODEX_START_HERE.md`, `NOVA_NATIVE_DATAVIZ_PRODUCTION_SPEC.md`, `NOVA_NATIVE_DATAVIZ_DESIGN_SYSTEM.md`, `NOVA_NATIVE_DATAVIZ_DATA_CONTRACTS.md`, `NOVA_DATAVIZ_INTEGRATION_MAP.md`, `NOVA_V4_CODE_AUDIT_EVIDENCE.md`, and `NOVA_NATIVE_DATAVIZ_QA_MATRIX.csv` in full. Then inspect the latest Nova source and scoped `AGENTS.md`. Implement the native Chart + Dashboard replacement for Superset in dependency-ordered work packages, maintaining current PostgreSQL metadata authority, StarRocks/Ranger authorization, shared Viz Core and existing Neva harness. Preserve Dataviz full canvas and Agent Artifact Panel using one document model, 12-column GridStack, ECharts, TanStack, typed semantic datasets/filters, revisioned publish, granular user/role sharing, and secure cross-chat live collaboration. Use only genuinely OSS required libraries. Build real tests, security/failure checks, migration reports and evidence. Do not claim completion until release gates pass; do not silently drop legacy assets or run destructive tests on unapproved data. Start by auditing the checkout and reporting WP00 with concrete source paths and dependencies; then continue implementing what is safe in the current environment.
