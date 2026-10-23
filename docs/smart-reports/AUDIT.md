# Current-state audit

The initial source inspection was read-only. Implementation started afterward in
the active `feat/intelligent-studio-harness-v2` checkout, with an existing dirty
working tree. Baseline patch and tracked-file copies were saved outside the repo
before edits. No feature worktree has been created.

## Verified owners

| Concern | Existing owner and observed behavior |
| --- | --- |
| Semantic execution | `backend/app/modules/intelligence/semantic_views.py`: versioned definitions, caller access, deterministic plan execution through QueryService |
| SQL compilation | `backend/app/modules/agents/semantic/`: IR, plan validation, aggregation, comparison and window transforms |
| Metadata coordination | `backend/app/modules/intelligence/engine_repository.py`: Redis metadata leases; no cross-table transaction assumption |
| Artifacts | `backend/app/modules/agents/artifact_repository.py`: saved SQL/chart configuration; legacy refresh has a 500-row display limit |
| Dashboards | `backend/app/modules/agents/dashboard_repository.py`: 6-column, 4-row tile layout and artifact references |
| Conversation execution | `backend/app/modules/assistant/`: shared bounded inference loop, evidence tracking, ordered content and consent |
| Studio navigation | `frontend/src/routes/studio.tsx` and `frontend/src/features/studio/index.tsx`: existing chat deep links and Studio shell |
| Charts/tables | ECharts and TanStack Table already installed; map adapter belongs to Studio Intelligent UI |
| Durable payloads | Existing managed storage, including object artifact store; Redis is not durable report storage |
| Scheduled execution | Existing task orchestration and deterministic internal handler registry |

## Local verification prerequisites

Two existing projects were inspected and retained:

- `nova-smart-unlimited`: disposable integration FE MySQL 39030, FE HTTP 38030,
  Arrow 39408, Redis 36379, MinIO 39000. This is a stock engine; passing its tests
  does not establish Ranger enforcement.
- `docker`: governed patched FE MySQL 29030, Redis 6379, MinIO 9000 and Ranger
  6080. Preserve its development data and volumes.

The shared pytest fixture can create and tear down a stack when endpoints are
missing. Endpoint reuse must therefore be verified before invoking it. New report
engine tests create uniquely named temporary databases only in the disposable
integration project and clean up only their own data.

The installed macOS SciPy binary fails to load a Mach-O section. Reinstalling the
locked wheel and trying a separate Python 3.11 environment reproduced the failure.
A Linux test runner reuses the checkout read-only; its venv, cache and coverage
output are outside that mount. It does not provision supporting services.

DevMesh reported no project session to inherit. Its Codex adapter also reported a
version mismatch. No handoff or collaboration readiness is assumed, and no DevMesh
configuration was changed.

## Preservation review

The initial baseline is preserved outside the checkout, and the complete pre-merge
working tree/index is also retained in Git stash
16bc738b9e074cca72ea2fcb28b7d820fe7738ba. Upstream integration uses a separate
merge commit; selective feature staging excludes the user's original changes.
The original staged waiting-lifecycle test is retained in the stash's second
parent and restored to the index after feature delivery.

Feature ownership overlaps assistant service/intelligence, agent harness
repository/worker/tool catalog, and the existing map surface. Stage only report
hunks from those files. The user's unlimited-agent, ML routing, business-map and
number-format work must remain outside this feature commit. Baseline differences
also contain upstream integration; they are not automatically owned by this task.
