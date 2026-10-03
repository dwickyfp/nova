# Intelligence Engine delivery and acceptance

Implementation is in progress. This document records ownership and acceptance
boundaries; it is not a completion report.

## Ownership

| Owner | Extension |
| --- | --- |
| Intelligence | Context records, monitoring, News, investigations, decisions, lineage and outcomes |
| Agent memory and proposals | Immutable knowledge revisions, source evidence, conflicts and reviewed promotion |
| Semantic Views | Discovery candidates, preview, validation, regression and recoverable publication |
| Shared assistant | Bounded Studio/Smart retrieval, provider selection and existing tool execution |
| ML engine | Numerical analysis, simulations and persisted numerical run references |
| Task orchestration | Allowlisted monitor, consolidation and outcome handlers |
| Access control | Business approval policy after existing data authorization and consent |

`NOVA_SYSTEM` remains the relational metadata owner. Redis coordinates locks,
cursors, sessions and wakeups. Memory IDs and the private-memory API remain
compatible. Schemas are additive and idempotent, with matching runtime,
bootstrap and migration definitions. Deploy schemas, compatible services,
workers and surfaces in that order. Disabling schedules preserves history.

## Execution boundaries

New schedules default to disabled and a 15-minute cadence. Cycles are bounded
by 100 items, three investigations, 20 governed queries and 120 seconds, with
provider and ML limits applied by their existing owners. Private consolidation
requires the originating principal, role, session and security-context version.
Scheduled domain work requires an explicit service-principal binding.

Graph endpoints, lineage and shared decisions recheck the viewer's access.
Matching roles do not grant another principal's cached results. Stored query
evidence must be reproducible under the viewer's security scope. Dataset
revocation, masks or changed evidence fail closed. Publication and approval
operations bind exact revisions and preserve retry-safe durable intent.

Investigations pin the originating News revision; News pins the monitor
revision. Later News status changes or monitor configuration edits cannot
replace a decision's baseline. Arithmetic contributions reconcile separately
from causal claims. Timeline events establish temporal association only.
Numerical causal estimation requires an explicit supported randomized design.
Driver analysis requires additive metrics and complete, untransformed segments.
Outcome reads reauthorize the pinned prediction and baseline as well as actuals.
Effectiveness groups monetary dimensions by metric, currency and semantic
version; historical outcomes without those identifiers are not mixed into an
aggregate. Decision context references pin source revisions and reauthorize
them during graph reads.

External inventory transfers, campaign updates and rollbacks remain outside
this program. Decision options are recommendations and conditional simulations.
Outcome change is distinguished from attributable effect. Shared business
verification and semantic publication require authorized review.

## Acceptance matrix

| Gate | Evidence required before delivery |
| --- | --- |
| Backend | Full unit coverage, changed-file Ruff, affected eval trajectories and agent scorecard |
| Metadata and engine | Seeded StarRocks/Redis/storage integration, fresh/existing schema migration and boundary checker |
| Ranger | Patched FE, two roles, same-role principals, filters, masks, revocation, delegation, caches and stored evidence |
| Studio baselines | Existing L0 and L2; preserve L3 runner/baseline, defer paid execution |
| Frontend | Lint, build, full coverage, Chromium component and actual API/browser flows |
| Learning | Frozen production-path B0/learning/B1/B2, independent gold, leakage checks and explicit narrative review |
| Business stories | Jakarta stockout and payment degradation through investigation, approval, lineage and outcome |
| Delivery | Fetch/integrate current main, clean-checkout verification, program-only commit/push and final main integration |

The failure-mode matrix includes ambiguity, contradictions, stale knowledge,
semantic changes, unsupported causality, unrelated deployments, seasonality,
small samples, policy denial, dataset denial, partial worker writes, duplicate
incidents, overlapping decisions, missing outcomes, provider outages,
correction reversal, malicious memory and unsupported external claims.

No paid live result, verified GitHub Actions result, final clean-checkout result,
or complete acceptance is claimed by this document.
