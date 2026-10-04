# Studio lifecycle closure engineering report

## 1. Result and branch

The remediation extends the existing governed Studio lifecycle from successful
semantic evidence through Investigation, scenarios, Decisions, supervised
Actions, Outcomes, learning proposals, and pinned reports. Chat remains the
primary interface with Activity, Evidence, and Context.

Work is isolated on `feat/studio-agentic-lifecycle-closure`, based on
`feat/agentic-business-os` at `17ab3d6`, with fetched `main` at `d683134`
integrated before implementation. The original checkout and foundation worktree
were preserved. Production feature flags remain false. Analytical execution
remains explicitly unavailable by the approved infrastructure decision.

## 2. Architecture

AssistantLoop still owns planning, provider/tool budgets, and consent. AgentControl
and its journals own Smart participants and admission. Intelligence owns Scenario
and Action adapters, canonical Investigation/Decision/Outcome records, and
revision checks. Context Graph owns projections and authority. Existing semantic
time resolution, numerical execution, Studio automation, proposal, release,
managed storage, and StarRocks persistence owners are extended in place.

Planner output instructions derive from the supplied `_plan_schema()` fields
and enums. The existing safe fallback and provider-call budget remain intact.
Business result hooks consume successful validated semantic execution without
adding another inference loop or classifier call.

## 3. Changed files

The exact repository-relative file manifest is included below. The principal
new modules are `agents/business_results.py`, `agents/doctor.py`,
`agents/quality_application.py`, `agents/semantic/learning_sources.py`,
`intelligence/evidence.py`, `intelligence/context_sources.py`,
`intelligence/automation_action.py`, and `core/studio_capabilities.py`.

## 4. Storage and migration

No new DDL, tables, or columns are introduced by this remediation. Existing
foundation migrations and initializers remain the schema owners. New bindings,
anchors, concrete execution contexts, report snapshots, comparison Monitor pins,
and proposal application references evolve existing JSON payloads. Missing legacy
Mission fields are synthesized from the original scope. Historical canonical
scopes and existing session/security columns retain their meanings.

## 5. Mission continuation

The deterministic policy returns `continue`, `new`, or `none` with a public
reason code. Replay identity resolves first. Conflicting explicit new/continue
controls are rejected. Explicit continuation can reopen completed work;
cancelled work cannot continue. Matching successful semantic anchors, canonical
references, and screen follow-ups can continue active work. A different semantic
target or filtered population takes precedence over implicit continuation.
Unrelated or ambiguous objectives create a new Mission; lightweight answers
can remain without a Mission. Smart children inherit the root Mission.

## 6. Resume and security

Missions preserve durable owner scope, current execution binding, historical run
bindings, exact canonical revision pins, release pins, and bounded anchors.
Owner/role-scoped summaries expose explicit resume. Revision-checked, idempotent
resume runs under existing admission fencing and reauthorizes thread, agent,
semantic versions, canonical records, releases, and resources before binding
current credentials. Active old-session work and uncertain mutations require
reconciliation. Successful and refused resumes are audited.

Historical runs and canonical records use their recorded scopes through a narrow
Mission-authorized path. General Intelligence scope checks are preserved.
Original attachment grants and source digests are rechecked through the original
binding while the current principal/role must still own the thread. This grants
no old-session execution authority. New work uses current credentials, current
policy, and fresh consent; historical approvals do not authorize new dispatch.
Creation and turn replay resolve operation identity across the owner/role scope
before selecting the current binding. Old-session replays return HTTP 409 and
cannot overwrite the rebound Mission, including lightweight follow-ups.

## 7. Investigation canonicalization

The existing time owner accepts an injected clock and named timezone, resolves
concrete windows, and persists their execution context before SQL dispatch.
Compiler SQL and provenance share identical bounds. In-progress periods compare
elapsed local calendar spans. Month/leap boundaries clamp both periods to their
shared available span; DST differences in UTC duration are explicit warnings.

Only successful validated, complete semantic execution can derive a seed.
Unsafe plans return structured requirements with established values prefilled.
An exactly compatible authorized Monitor is preferred; otherwise a disabled
one-time comparison uses unknown sample count. Unknown count lowers statistical
confidence while permitting valid arithmetic decomposition. Scheduled Monitors
still require reviewed count semantics.

Mission, semantic identity, target, fixed windows, and validated plan fingerprint
deduplicate automatic work. The comparison records its original configuration
and Monitor revision. Completed and interrupted retries recover these pins
before considering newer Monitor configuration, then repair missing Mission
linkage. Arithmetic contribution and association retain their causal labels.
Cross-binding recovery requires the durable internal Mission/seed operation proof
and exact dependency revisions. Historical scope alone cannot grant access.
Queries use current credentials; original canonical scopes remain immutable.

## 8. Scenario adapters

Typed `ScenarioAdapter`, `ScenarioContext`, and `ScenarioExecution` contracts
live inside Intelligence. Registration validates kind/version, parameter schemas,
actions, target-metric policy, and currency requirements. Unit economics owns
revenue reconciliation, published metric currency checks, and gross-profit
calculation through the existing numerical executor. Published monetary targets
such as `net_booked_revenue` retain legacy support.

Decision consumes canonical adapter results, stores additional scalar outcomes
in `effects`, and retains optional legacy gross profit. Governed Decision context
is server-derived; client prediction authority is rejected. Every registered
definition uses the existing schema renderer and generic persistence path. A
test-only second adapter and frontend fixture prove the extension contract.

## 9. Evidence and Context

`EvidenceEnvelope` combines existing Evidence Health with exact published semantic
identity, metrics, dimensions, concrete windows, timezone, filter shapes,
warnings, and evidence references. The same bounded envelope is persisted and
streamed for equivalent replay. Filter literals, credentials, and private
reasoning are excluded. Evidence selection resolves the exact semantic identity
and metric, including an accessible historical published version and bounded
neighborhood. Authority and conflict display use that selected graph.

Canonical Mission, Action, deliverable, release, and query-pattern projections
use their actual owners. Existing dashboard, artifact, and document references
remain subject to their access contracts and do not gain reviewed authority.
Changed surfaces cover light/dark themes, narrow/desktop layouts, keyboard and
focus, replay, loading, partial evidence, and permission denial.

## 10. Agent Doctor

Doctor compares compatible frozen datasets, case revisions, scorer versions,
and promotion gates. Structured findings identify the known-good baseline,
first bad evaluated release, current release, affected cases/traces, changed
dependencies, and measured regressions. Authorized semantic inspection reports
deterministic alias collisions and definition changes. Explanations remain
hypotheses. Findings include reviewable regression-case candidates and concrete
remediation patches.

Reviewed application creates an agent draft or existing Semantic proposal with
stable operation identity and persisted application references. Retries recover
the same application; stale bases fail closed. Evaluation, promotion gates,
and human publication remain required.

## 11. Governed learning

Source adapters normalize semantic usage, published verified queries, Mission
deliverables, Decision/Outcome evidence, and Context query patterns. Bounded
observations retain source identity, authority, validity, freshness, and redacted
evidence. Current principal/role authorization and learning opt-out apply to
each source. The existing proposal workflow remains the publication owner.
Frequency cannot upgrade inferred concepts to verified truth. Unrestricted SQL,
sensitive literals, and private document bodies are excluded.

## 12. Actions

`automation-v1` extends the existing Studio automation owner with typed preview,
stable creation identity, execution, configuration readback, and consented
disable compensation. It requires agent ownership and the current authorized
binding; initial delivery remains internal to Studio. Registry-owned configuration,
receipts, policy inputs, and verification preserve `monitor-v1` compatibility.

Both adapters retain dispatch fencing and destructive consent. Uncertain
mutations require verification rather than another dispatch. Scheduled operations
require the active published semantic version; historical reads can display an
authorized pin without granting stale scheduled execution. Verified configuration
is an operational effect and cannot establish causal business improvement.

## 13. Analytical workspace

`UnavailableAnalysisExecutor` remains selected. Capability reporting separates
the feature flag from executor availability. Disabled execution reports
`FEATURE_DISABLED`; enabled execution without isolation reports
`BLOCKED_BY_INFRASTRUCTURE` and returns HTTP 503 with no rows or artifacts.
Refusals remain audited. Contract tests use an isolated test adapter only.
There is no insecure local, subprocess, or thread fallback.

## 14. Deliverables

Deterministic `analysis_summary`, `investigation_report`, `scenario_comparison`,
and `outcome_report` join existing Decision memos and action plans. Reports freeze
structured factual snapshots, exact revisions, and fingerprints from authorized
pinned work, without recomputing business analysis. Operation collisions and
unavailable/oversized sources fail with bounded errors. Forecasts require a
canonical forecast artifact and are omitted otherwise.

## 15. Flags and operations

Four documented false-default flags are forwarded to the Compose API/task worker
and existing Smart-worker launch path. A shared safe capability helper provides
startup diagnostics and additive Studio capability data. Enabled flags, usable
capabilities, and executor availability remain separate. No flag substitutes for
authorization, consent, or quality opt-in.

## 16. Validation

Commands below run from `backend/` unless another directory is named. Baseline
and acceptance artifacts reside in `/tmp/nova-lifecycle-*`; private runtime
environment files contain disposable credentials and are not committed.

| Check | Exact command | Result |
| --- | --- | --- |
| Locked dependencies | `uv sync --locked` | Python 3.11.13; 119 resolved, 113 audited |
| Unit coverage | `uv run pytest tests/unit --cov=app --cov-branch --cov-report=xml:/tmp/nova-lifecycle-unit-delivery-coverage.xml --cov-report=term-missing --junit-xml=/tmp/nova-lifecycle-unit-delivery.xml` | 6,262 passed; 3 warnings; branch-aware total coverage 58% |
| Deterministic evals | `uv run pytest tests/eval --junit-xml=/tmp/nova-lifecycle-eval-delivery.xml` | 288 passed |
| Eval scorecard | `uv run python -m tests.eval.report` | 48/48 scenarios; 165/165 checks |
| Blocking Ruff | `uv run ruff check --force-exclude <65 changed Python paths>` | Passed; task edits and full branch versus fetched `main` both checked |
| User-data system access | `uv run python scripts/check_user_data_system_access.py` | Passed; no system/root shortcuts |
| Fixture benchmarks | `NOVA_GOVERNED_PERFORMANCE_REPORT=/tmp/nova-lifecycle-performance-delivery.json uv run pytest tests/benchmark/test_assistant_loop.py tests/benchmark/test_governed_studio_performance.py -q -s` | 13 passed |
| Stock-engine benchmark | `NOVA_ORCH_SR_HOST=127.0.0.1 NOVA_ORCH_SR_PORT=45930 uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env pytest tests/benchmark/test_assistant_engine.py -q -s -rs` | 1 passed; 20 samples |
| Stock seed | `uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env bash tests/integration/seed_engine.sh` | Passed |
| Stock core | `uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env pytest tests/integration -m engine --ignore=tests/integration/test_intelligence_studio_live.py --ignore=tests/integration/test_studio_business_metadata.py --ignore=tests/integration/test_automation_action_metadata.py -v -rs --log-cli-level=WARNING --junit-xml=/tmp/nova-lifecycle-stock-core-delivery.xml` | 143 passed initially; missing probes rerun below |
| Stock fixture repair | `uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env pytest tests/integration/test_external_catalogs_l3.py tests/integration/test_inverted_index_l3.py tests/integration/test_migration_l3.py -m engine -v -rs --log-cli-level=WARNING --junit-xml=/tmp/nova-lifecycle-stock-prerequisites-delivery.xml` | 42 passed; 1 unsupported stock-engine capability skipped |
| Stock Mission/automation | `uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env pytest tests/integration/test_studio_business_metadata.py tests/integration/test_automation_action_metadata.py -m engine -v -rs --tb=short --junit-xml=/tmp/nova-lifecycle-stock-mission-delivery.xml` | 6 passed, including real new login and old-session replay refusal |
| Stock Studio API | `uv run --env-file /tmp/nova-lifecycle-stock-acceptance.env pytest tests/integration/test_intelligence_studio_live.py -m engine -v -rs --log-cli-level=WARNING --tb=short --junit-xml=/tmp/nova-lifecycle-stock-studio-delivery.xml` | 2 passed in 1,045.55 seconds |
| Patched-FE/Ranger | `uv run --env-file /tmp/nova-lifecycle-governed-acceptance.env pytest tests/integration/test_governed_studio_ranger.py -v -rs --tb=short --junit-xml=/tmp/nova-lifecycle-ranger-closure-delivery.xml` | 5 passed in 202.25 seconds; no skips |
| Ranger journey benchmark | `uv run --env-file /tmp/nova-lifecycle-governed-acceptance.env python -m tests.benchmark.governed_studio_journey --samples 3 --report /tmp/nova-lifecycle-ranger-journey-delivery.json` | 3/3 passed |
| Frontend locked setup (`frontend/`) | `npm exec --yes --package=pnpm@11.8.0 -- pnpm install --frozen-lockfile` and `npm exec --yes --package=pnpm@11.8.0 -- pnpm exec playwright install --with-deps chromium` | Passed; Node 22.22.3, pnpm 11.8.0 |
| Frontend lint/build (`frontend/`) | `npm exec --yes --package=pnpm@11.8.0 -- pnpm lint` and `npm exec --yes --package=pnpm@11.8.0 -- pnpm build` | Passed; lint has 25 nonblocking warnings |
| Frontend coverage (`frontend/`) | `npm exec --yes --package=pnpm@11.8.0 -- pnpm test:coverage` | 1,085 passed across 139 files |
| Changed-surface browser matrix (`frontend/`) | `npm exec --yes --package=pnpm@11.8.0 -- pnpm exec vitest run src/features/studio/lifecycle-surfaces.test.tsx --browser.headless` | 24 passed |
| Engine patch integrity (repository root) | `bash patches/starrocks/verify.sh` | Passed |

The exact changed-file Ruff command resolves both tracked edits and new files:

```sh
python - <<'PY'
from pathlib import Path
import subprocess
root = Path.cwd().parent
names = set(subprocess.check_output(
    ['git', 'diff', '--name-only', '--diff-filter=ACMR', 'origin/main'], cwd=root, text=True
).splitlines())
names.update(subprocess.check_output(
    ['git', 'ls-files', '--others', '--exclude-standard'], cwd=root, text=True
).splitlines())
files = sorted(str(root / name) for name in names
               if name.startswith('backend/') and name.endswith('.py'))
raise SystemExit(subprocess.run(
    ['uv', 'run', 'ruff', 'check', '--force-exclude', *files]
).returncode)
PY
```

The stock suite runs in disjoint groups to serialize real runtime work. Its
catalog, inverted-index, and migration probes initially skipped because a
benchmark-only port override was read as a string by legacy fixtures. Removing
that override restored the real probes; the engine benchmark supplies it inline.
Other allowed skips cover explicit opt-in provider/custom-SQL or cross-cluster
fixtures, shared-data prerequisites, unsupported stock `CREATE TASK`, and Ranger
cases exercised separately against the patched FE. Deduplicating the final JUnit
reports by test class/name yields 193 passed and 12 skipped across 205 distinct
stock-engine cases; 30 non-engine cases are deselected.

Both Compose configurations passed `config --quiet`, from the repository root:

```sh
docker compose --env-file /tmp/nova-lifecycle-ranger.env -f docker/docker-compose-engine.yml config --quiet
docker compose --env-file /tmp/nova-lifecycle-ranger.env -f docker/docker-compose-engine.yml -f docker/docker-compose.dev.yml config --quiet
```

The 24-case changed-surface Chromium matrix covers themes, narrow/desktop
layouts, keyboard/focus, replay, loading, partial data, and denial. Additional
Context tests verify contrast and exact selection; setup tests verify prefilled
windows down to milliseconds. Final frontend coverage passed after repairs to
animation timing, dynamic-import test waits, and an insufficient-contrast link.
Initial stock failures from an obsolete recency expectation, a transient materialized
view trial, and legacy monetary metric support were repaired and rechecked.
An initial Ranger benchmark attempt had one HTTP 504 under concurrent build
pressure; the serialized three-sample rerun passed. No assertion or gate was weakened.
A restarted backend briefly remained on the FE blacklist after its container
healthcheck passed; actual metadata-read readiness restored the acceptance setup.
The new automatic-resume fixture initially supplied Python-only plan shapes,
which correctly triggered planner fallback. It now uses validated wire JSON and
seeds rows within the exact local comparison bounds. Live acceptance then exposed
a tuple/JSON-array configuration mismatch after persistence. Canonical JSON
comparison and plan normalization repaired it; two JSON round-trip unit cases
and the real automatic-resume case passed before final broad rechecks.

All final gates above passed. Results are local acceptance results. GitHub Actions has not been verified
by these commands. Disposable projects, policies, users, roles, ports, and volumes
were isolated from the existing `nova-*` environment. Both test FEs use 2 GiB heaps.

## 17. Eval comparison

The locked baseline passed 6,022 backend unit tests, 283 deterministic evals,
48/48 scorecard scenarios and 165/165 checks, 1,027 frontend tests across 135
files, and 13 fixture benchmarks. One real-engine benchmark was skipped in the
baseline and subsequently run against the isolated stock engine.
The final unit count is 6,262 (+240), deterministic eval count is 288 (+5), and
the scorecard remains 48/48 and 165/165. Frontend coverage increased to 1,085
tests (+58) across 139 files (+4). Stock-engine acceptance passed 193 distinct
cases with 12 documented skips. Patched Ranger acceptance is reported separately.

## 18. Benchmark comparison

The native fixture keeps provider/tool/participant counts unchanged: 2 provider
calls, 0 tool calls, 1 participant, 50 total tokens, 20 context tokens, and 0
metadata reads. Pure evidence/Mission projection adds no provider calls.
Fixture timing and real-service journey timing are reported separately because
host load and setup overhead differ. Real inference vendor latency is outside
these deterministic and scripted acceptance contracts.

| Measurement | Baseline p50 / p95 | Delivered p50 / p95 | Samples |
| --- | --- | --- | --- |
| Native AssistantLoop fixture | 0.732875 / 1.055708 ms | 0.969791 / 2.254000 ms | 25 each |
| Pure evidence/Mission projection | 0.164875 / 0.214417 ms | 0.259667 / 0.296542 ms | 25 each |
| Deterministic case scorer | 0.022125 / 0.024500 ms | 0.021875 / 0.039292 ms | 25 each |
| Stock real-engine path | Baseline unavailable | 204.221375 / 302.392875 ms | 20 |
| Patched-FE/Ranger journey call phase | Paired baseline unavailable | 125,287.810 / 131,351.384 ms | 3 |

Native fixture p50 increased by approximately 0.237 ms and projection p50 by
0.095 ms on this host. These distributions include shared-host load and establish
no causal performance regression or production SLA. The three real journey
samples are 123,645.064, 125,287.810, and 131,351.384 ms. With fewer than 20 samples,
reported journey p95 is the maximum. Collection, setup, and teardown are excluded;
Nova HTTP is in-process ASGI while SQL and Ranger policy I/O use real services.
Complete journey provider/tool/participant/token/metadata totals are unavailable.
Synthetic count-budget violations intentionally fail their negative fixtures;
missing measurements stay unavailable. All 13 benchmark tests pass.

## 19. Compatibility

Regression tests cover legacy `simulation` inputs, published revenue aliases,
generic Scenario persistence, optional gross profit, original Mission synthesis,
and absent additive fields in Decision/request/approval/Action-event digests.
Monitor Action contracts keep their original dumps. Historical records retain
their exact scope and revision; current lifecycle operations reauthorize their
pins and fence stale bindings/revisions. No access or causal label is upgraded
through deserialization, reuse, or a verified configuration.

## 20. Limits

DevMesh project configuration is present, but dynamic session/handoff tools were
unavailable in this chat. Repository-local implementation and validation continued
against the approved brief; no external handoff was retrieved or invented.
No live inference vendor quality or production SLA is established by fixture
benchmarks or scripted real-engine acceptance. Production flags remain false.
Historical legacy records without release pins retain unknown release identity.
External automation delivery is excluded by the approved initial scope.

## 21. Infrastructure blocker

Analytical sandbox execution is `BLOCKED_BY_INFRASTRUCTURE`: no approved isolated
executor exists to enforce process/container resource limits, network/filesystem
isolation, cancellation, cleanup, and credential-free input/output. The approved
plan explicitly retains this refusal. Other required acceptance failures must be
resolved before delivery and are not waived by this sandbox decision.

## File manifest

- `HOW_TO_RUN.md`
- `backend/.env.example`
- `backend/app/agent_worker/__main__.py`
- `backend/app/core/studio_capabilities.py`
- `backend/app/main.py`
- `backend/app/modules/agents/automations.py`
- `backend/app/modules/agents/business_results.py`
- `backend/app/modules/agents/child_timeline.py`
- `backend/app/modules/agents/doctor.py`
- `backend/app/modules/agents/harness_repository.py`
- `backend/app/modules/agents/harness_worker.py`
- `backend/app/modules/agents/mission.py`
- `backend/app/modules/agents/mission_router.py`
- `backend/app/modules/agents/mission_schema.py`
- `backend/app/modules/agents/quality.py`
- `backend/app/modules/agents/quality_application.py`
- `backend/app/modules/agents/quality_router.py`
- `backend/app/modules/agents/resource_delegation.py`
- `backend/app/modules/agents/router.py`
- `backend/app/modules/agents/semantic/compiler.py`
- `backend/app/modules/agents/semantic/learning_sources.py`
- `backend/app/modules/agents/semantic/time_ranges.py`
- `backend/app/modules/agents/studio_router.py`
- `backend/app/modules/agents/studio_schemas.py`
- `backend/app/modules/agents/studio_service.py`
- `backend/app/modules/agents/tools/semantic_query.py`
- `backend/app/modules/assistant/analysis_workspace.py`
- `backend/app/modules/assistant/intent.py`
- `backend/app/modules/assistant/planning.py`
- `backend/app/modules/assistant/schemas.py`
- `backend/app/modules/assistant/service.py`
- `backend/app/modules/assistant/tools/_boundary.py`
- `backend/app/modules/intelligence/action_contracts.py`
- `backend/app/modules/intelligence/action_router.py`
- `backend/app/modules/intelligence/actions.py`
- `backend/app/modules/intelligence/automation_action.py`
- `backend/app/modules/intelligence/autopilot.py`
- `backend/app/modules/intelligence/context_graph.py`
- `backend/app/modules/intelligence/context_sources.py`
- `backend/app/modules/intelligence/contracts.py`
- `backend/app/modules/intelligence/decisions.py`
- `backend/app/modules/intelligence/engine.py`
- `backend/app/modules/intelligence/engine_router.py`
- `backend/app/modules/intelligence/evidence.py`
- `backend/app/modules/intelligence/monitor_action.py`
- `backend/app/modules/intelligence/scenarios.py`
- `backend/app/worker/__main__.py`
- `backend/tests/eval/test_automation_action_trajectory.py`
- `backend/tests/eval/test_studio_lifecycle_closure.py`
- `backend/tests/integration/test_automation_action_metadata.py`
- `backend/tests/integration/test_governed_studio_ranger.py`
- `backend/tests/integration/test_studio_business_metadata.py`
- `backend/tests/unit/test_agent_child_timeline.py`
- `backend/tests/unit/test_agent_doctor.py`
- `backend/tests/unit/test_automatic_investigation.py`
- `backend/tests/unit/test_automation_actions.py`
- `backend/tests/unit/test_concrete_semantic_windows.py`
- `backend/tests/unit/test_context_lifecycle_sources.py`
- `backend/tests/unit/test_governed_learning_sources.py`
- `backend/tests/unit/test_intelligence_decisions.py`
- `backend/tests/unit/test_mission_release_context.py`
- `backend/tests/unit/test_scenario_registry.py`
- `backend/tests/unit/test_semantic_usage_opt_out.py`
- `backend/tests/unit/test_studio_analysis_workspace.py`
- `backend/tests/unit/test_studio_missions.py`
- `backend/tests/unit/test_studio_resource_delegation.py`
- `backend/tests/unit/test_studio_runtime_capabilities.py`
- `dev.sh`
- `docker/.env.example`
- `docker/docker-compose-engine.yml`
- `docs/arch-15-governed-studio.md`
- `docs/governed-studio-operations.md`
- `docs/reports/studio-lifecycle-closure-20261004.md`
- `frontend/src/features/agents/agent-detail/quality-doctor.test.tsx`
- `frontend/src/features/agents/agent-detail/quality-doctor.tsx`
- `frontend/src/features/agents/agent-detail/quality-proposal-draft.tsx`
- `frontend/src/features/agents/agent-detail/quality-proposal-review.tsx`
- `frontend/src/features/agents/agent-detail/quality-tab.tsx`
- `frontend/src/features/agents/api.ts`
- `frontend/src/features/agents/quality-api.ts`
- `frontend/src/features/assistant/types.ts`
- `frontend/src/features/assistant/use-assistant-transcript.ts`
- `frontend/src/features/intelligence/action-lifecycle.test.tsx`
- `frontend/src/features/intelligence/action-lifecycle.tsx`
- `frontend/src/features/intelligence/action-preview.tsx`
- `frontend/src/features/intelligence/context-api.ts`
- `frontend/src/features/intelligence/context-inspector.tsx`
- `frontend/src/features/intelligence/decision-compose.test.tsx`
- `frontend/src/features/intelligence/decision-compose.tsx`
- `frontend/src/features/intelligence/learning-api.ts`
- `frontend/src/features/intelligence/lifecycle-api.ts`
- `frontend/src/features/intelligence/scenario-fixtures.test-support.ts`
- `frontend/src/features/intelligence/scenario-schema.test.ts`
- `frontend/src/features/intelligence/scenario-schema.ts`
- `frontend/src/features/studio/context-panel.test.tsx`
- `frontend/src/features/studio/context-panel.tsx`
- `frontend/src/features/studio/evidence-health.test.tsx`
- `frontend/src/features/studio/evidence-health.ts`
- `frontend/src/features/studio/evidence-panel.tsx`
- `frontend/src/features/studio/investigation-start.test.tsx`
- `frontend/src/features/studio/investigation-start.tsx`
- `frontend/src/features/studio/lifecycle-surfaces.test.tsx`
- `frontend/src/features/studio/mission-objects.test.tsx`
- `frontend/src/features/studio/mission-objects.tsx`
- `frontend/src/features/studio/mission-panel.tsx`
- `frontend/src/features/studio/mission-turn-controls.test.tsx`
- `frontend/src/features/studio/mission-turn-controls.tsx`
- `frontend/src/features/studio/studio-capabilities.test.tsx`
- `frontend/src/features/studio/studio-capabilities.tsx`
- `frontend/src/features/studio/studio-chat.tsx`
- `frontend/src/features/studio/studio-intelligence.test.tsx`
- `frontend/src/features/studio/studio-intelligence.tsx`
- `frontend/src/features/studio/studio-runtime-capabilities.tsx`
- `frontend/src/features/studio/studio-transcript.ts`
- `frontend/src/features/studio/workflow-api.ts`
- `frontend/src/features/studio/workflow-rail.tsx`
