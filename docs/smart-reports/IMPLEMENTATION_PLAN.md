# Implementation and acceptance map

The user-approved scope is P0–P2. The implementation uses the active checkout and
normal application routes, without rollout flags. Slice order moves migration
from S15 before P0 hardening. A slice is delivered only after the complete gates
and active-branch commit/push recorded in STATUS.md.

| Slice | Implementation | Acceptance owner |
| --- | --- | --- |
| S00 | Baseline preservation, ownership audit, upstream integration | AUDIT.md; final selective diff review |
| S01 | Pydantic, generated schema, Zod, shared fixture and capabilities | Contract and frontend schema tests |
| S02 | Append-only journal, checksums, operation replay, grants, Redis sessions/handoff | Lifecycle/concurrency tests; real Redis integration |
| S03 | Governed SemanticPlan execution, pinned revisions/clock, exact decimals, totals | Golden SQL; patched-FE/Ranger callers |
| S04 | Shared renderer, KPI/table/ECharts/map, explicit result states | Browser, theme, keyboard and width review |
| S05 | Shared Studio shell, Reports routes, independent/copied and shared/handoff sessions | Browser refresh, handoff, deep-link journey |
| S06 | Server smart_report blocks, persistence/SSE/replay, server-resolved Ask context | Scripted-provider stream; real governed Bandung context |
| S07 | Prompt planning, source catalog, native/scoped filters | Live external model through Reports UI; enumeration tests |
| S08 | Manual/AI typed patches, preview, private drafts, review, undo/redo, publish/restore, sharing | Editor/publisher API and browser acceptance |
| S15 early | Artifact provenance/copy; restartable metadata-only dashboard migration | 103-source fixtures; actual one-source reconciliation; legacy regression |
| S09 | Query admission, cancellation, partial results, audit and metrics, responsive/reflow checks | Full repository gates; UI judge; eight-widget latency |
| S10 | Cross-filter, drill down/by/detail, saved views, multi-page/reorder/resize | Reducer/interaction tests; grain-checked Ranger row detail |
| S11 | Calculation AST, fiscal/time comparisons, pivot/subtotals, advanced charts/table formatting | Seven independent engine cases over 11,037 rows; calendar and visual tests |
| S12 | In-batch deduplication, bounded concurrency, cancellation; policy-unverifiable result cache bypass | Two-identity isolation; real eight-widget latency |
| S13 | Evidence-based variance/contribution/unusual-value observations; contextual Ask | Precision, completeness, null/zero and revocation tests |
| S14 | All-page PDF/XLSX/PPTX, authorized downloads, subscriptions, threshold/cooldown, Nova activity | Browser downloads; real scheduled delegated execution; replay/revocation |
| S15 final | Private validated templates, version rejection and additive legacy compatibility | Template tests; report/version schema fixtures; legacy UI/API |

## Product boundaries

Report-local metrics never mutate global semantic definitions. No unauthenticated
sharing or unsupported forecast is offered. Cross-request result cache remains
unavailable while policy freshness cannot be verified. The capability registry
reports this reason; it is not a rollout switch.

Metadata migration preserves source dashboards/shares and creates a draft even
when mapping is missing. Mapping-required widgets cannot execute or publish.
Rollback retains all journal history, managed payloads and legacy source data.

## Delivery procedure

1. Run the required backend, frontend, seeded-engine and governed checks on the final tree.
2. Review security, UI findings, documentation and the pre-existing user changes.
3. Fetch remote main, integrate any missing changes and rerun affected checks.
4. Commit only the upstream merge and this feature's changes; preserve unrelated staging.
5. Fetch/check main again, push the active delivery branch and reload its local processes.
6. Record exact results, remaining prerequisites and cleanup status in STATUS.md.
