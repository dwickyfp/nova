# Architecture 15: Governed Studio workflow

The [operator guide](governed-studio-operations.md) covers migrations, rollout
controls, recovery, and acceptance checks for these contracts.

The workflow connects a question to investigation, evidence, scenarios, a
Decision, supervised action execution, verification, outcome observation, and
reviewable improvements. Each step extends its existing owner:

| Responsibility | Owner |
| --- | --- |
| Provider/tool iteration, bounds, consent | [AssistantLoop](../backend/app/modules/assistant/service.py), [shared tool gate](../backend/app/modules/assistant/tool_gate.py) |
| Smart participants, delegation, recovery | [AgentControl](../backend/app/modules/agents/agent_control.py), existing run journals |
| Releases and evaluation | [releases](../backend/app/modules/agents/releases.py), [quality](../backend/app/modules/agents/quality.py), [scorers](../backend/app/modules/agents/quality_scoring.py) |
| Missions and attachment grants | [Mission service](../backend/app/modules/agents/mission.py), [resource delegation](../backend/app/modules/agents/resource_delegation.py) |
| Investigation, Decision, Outcome, Context Graph | Existing [Intelligence service](../backend/app/modules/intelligence/engine.py) and [revision repository](../backend/app/modules/intelligence/engine_repository.py) |
| Scheduled monitoring/scoring | Existing task orchestrator and [internal handlers](../backend/app/modules/task_orchestration/internal_handlers.py) |

Durable metadata stays in flat `NOVA_SYSTEM` tables. Redis coordinates sessions,
leases, admission, and wakeups. Attachment bodies stay in existing message
attachment storage; references and grants do not copy bodies into another store.
Mission projection and Evidence Health assessment make no model calls.

## Controls and defaults

[Settings](../backend/app/core/config.py) define four independent controls:

| Environment variable | Default | Current effect |
| --- | --- | --- |
| `STUDIO_BUSINESS_WORKFLOW_ENABLED` | `false` | Enables Mission workflow, common tool-result health projection, and governed Smart attachment registration/delegation. Disabled Mission/resource routes return unavailable. |
| `STUDIO_ACTIONS_ENABLED` | `false` | Enables Action preview, review, dispatch, and verification. These operations return `503` when disabled; existing ledger read/cancellation paths retain their authorization checks. |
| `STUDIO_QUALITY_ENABLED` | `false` | Enables persisted quality observations and production trace scoring. Offline quality APIs and manifested-release promotion checks are separate from this switch. |
| `STUDIO_ANALYSIS_WORKSPACE_ENABLED` | `false` | Allows an isolated analytical executor when one exists. The shipped executor remains unavailable even if this flag is enabled. |

Per-agent production monitoring also defaults to disabled. Switches do not
authorize data access, satisfy consent, or demonstrate acceptance. Configure each
participating API/worker process explicitly; the engine Compose file does not
currently forward these four variables from `docker/.env`.

## Release manifests and promotion

Existing agent configuration history remains the draft/version owner. A release
manifest adds immutable, credential-free dependency content beside those
snapshots. It captures Semantic View versions/fingerprints, skill bodies/trust
metadata, tool schemas/classification and safe configuration digests, tool code
revisions, compiled instructions/prompt, resolved models, routing settings,
resource-definition digests, agent-policy fingerprint, and scorer-set version.
Its dependency fingerprint excludes `created_at`.

Runtime composition loads pinned configuration, prompt, skills, model selection,
routing, and Semantic Views. Existing owners check tool/model/resource contracts
and semantic identities. Missing or incompatible dependencies produce explicit
errors rather than silently adopting current definitions. A new active Semantic
View does not replace the version pinned by a manifested agent.

Search-index and Feature Group contracts are rechecked before each tool dispatch,
including prefetched reads. Feature Lookup selects the pinned version and rejects
an explicit request for another version. Both tools validate the returned version
and recheck live resource access before their results become public evidence.
Detected drift ends the turn without a model repair call or another mutation.

External models and MCP servers can change behavior without an immutable vendor
revision. Nova pins their observable identity/schema/configuration and rejects
detected drift. The manifest discloses external mutability; it cannot promise
reproducible vendor responses.

Authorization remains live. Sharing, session validity, exactly one active role,
security-context version, source access, Ranger policies, row filters, masks,
and consent use existing checks. The agent-policy fingerprint does not freeze
Ranger permissions. Caller data never uses agent-owner or bootstrap credentials.

Agents with a null `release_manifest_id` retain legacy update behavior and appear
unevaluated. For manifested agents, runtime changes use draft -> evaluation ->
publish. A direct runtime edit returns `409`. Publication requires the current
`config_revision`, draft version, and its `quality_run_id`; restoring a saved
configuration is another guarded publication.

Promotion checks the same manifest/scorer set, current mandatory case revisions,
and passing results for every mandatory critical case. At least one mandatory
critical case with a required behavioral assertion is needed. Changing or adding
a mandatory case invalidates the older evaluation. Frozen `PromotionGates` can
also require other mandatory/all cases, selected behavioral scorers, or measured
latency/count budgets. Additional quality and performance modes default to
`report_only`; their separate `gate_results` remain visible. Required unavailable
results block promotion. Overall run status and `promotion_eligible` are distinct.

## Agent Quality Lab

Cases belong to an agent and carry revision, prompt, provenance, mandatory/
critical flags, production matching mode, and deterministic assertions. Runs
freeze the manifest reference/fingerprint, case revisions/assertions, scorer set,
results, and trace references. The current set is `nova-agent-scorers:1`, with
scorer version `1`.

| Scorer | Recorded evidence used |
| --- | --- |
| `semantic_selection` | Selected semantic facts compared with expected facts |
| `tool_selection` | Required and forbidden tool names |
| `tool_arguments` | Recorded argument facts compared with the assertion |
| `numeric_consistency` | Numeric verification or supported finite claims with evidence references |
| `evidence_coverage` | Evidence count, completeness, and requested health threshold |
| `clarification_quality` | Recorded clarification facts |
| `task_completeness` | Recorded completion facts |
| `policy_compliance` | Recorded authorization/policy facts |
| `action_verification` | Recorded action verification facts |
| `latency` | Measured duration against `max_ms` |
| `efficiency` | Recorded counts against configured limits |

Each score is `pass`, `fail`, or `unavailable`. Missing required evidence cannot
pass a case. Registering a scorer does not prove that every production trace
contains its inputs. Offline evaluation uses the shared bounded loop and permits
read-only calls while denying mutations.

Quality observations distinguish measured dispatches from provider invocation
subtotals, reported token subtotals, and context-size estimates. Measurements
cover the current loop attempt. Native HTTP dispatch attempts include retries and
redirects. Missing response usage, custom transports, unobserved nested work,
resumed attempts, or an incomplete participant set leave affected totals
unavailable. Metadata reads count executed result sets; direct cursor access and
failed executions disclose incomplete coverage. Required budgets cannot pass on
a partial count or an estimate. Offline evaluations explicitly collect these
observations even when production scoring is disabled.

Evidence coverage thresholds use the public health labels `insufficient`,
`limited`, `moderate`, and `strong`. Missing or unrecognized recorded health
produces an unavailable comparison rather than a passing score.

Production scoring reads persisted assistant traces without invoking their tools.
It requires the global quality flag, per-agent opt-in, and an authorized role
execution binding. Defaults are sample rate `0.1`, at most `20` traces per cycle,
and a `60` minute cadence. Matching uses the exact prompt digest or an explicit
`all_traces` case. Principal, role, and security version scope trace selection.
A production run cannot serve as promotion evidence.
Non-passing recorded scores create review proposals, including performance
findings when their gate is report-only. A proposal preserves the finding and
labels suspected causes as hypotheses; it does not change the promotion gate.

Like feedback retains the verified-query candidate path. Dislike/failure
feedback creates reviewable diagnoses and regression suggestions. Agent Doctor
distinguishes observed dependency drift from scorer-based suspected causes.
Accepting a proposal does not publish a runtime change; the draft still needs
evaluation and guarded publication.

## Work Intent, Missions, and resources

The additive planner/request contract carries `ANSWER`, `ANALYZE`, `INVESTIGATE`,
`PLAN`, `RESEARCH`, or `ACT`. It uses the existing planner without a separate
classification call. Simple answers and one-stage analysis can remain lightweight.
Explicit creation or multistage planned work persists a Mission tied to the
thread and caller scope.

A Mission projects existing run events and canonical object revisions into
public stages. It stores run/object/evidence references, revision, projection
cursors, and cancellation state. Retry, reconnect, and follow-up reuse identity
unless `new_mission` is requested. Operation IDs with changed inputs conflict.
Projection reads bounded journal pages; it neither dispatches tools nor invents
stage completion from coordination prose. Cancellation uses existing run control.

Resources reference the original user attachment message/index, digest, name,
type, and size. Root access and child grants check principal, role, session,
security version, thread, root run, and participant lineage. `spawn_agent` and
`followup_task` accept optional `resource_refs`. A participant can grant only
resources it holds to a descendant; siblings do not inherit a grant. Loading
rechecks source identity/digest, existing attachment limits, and provider modality.
Public lists, coordination events, and durable Smart checkpoints carry references
rather than raw bodies. Attachment content remains untrusted input.

Mission deliverables use `CONFIG_STUDIO_DELIVERABLES` separately from query-backed
chart/table artifacts. Decision memos and action plans reference authorized
canonical objects and evidence. Saving a deliverable does not establish a new
business fact or authorize execution.

## Evidence and context authority

[Evidence Health](../backend/app/modules/assistant/evidence_health.py) is a
deterministic assessment with schema version `1`, rule version
`evidence-health-v1`, explicit assessment time, source facts, freshness, reasons,
and unknown signals. Replay uses persisted time and rejects assessments that
disagree with their facts. Legacy numeric confidence remains compatible and is
not a correctness probability.

Failed, unexecuted, or unknown execution, unresolved semantic ambiguity, or
unsupported numbers produce insufficient evidence. Partial/truncated coverage,
conflicting sources, known stale data, or missing published semantic identity
prevent strong evidence. Strong requires published identity, a verified query
reference, successful complete execution, recorded freshness, resolved ambiguity,
and supported numbers. Unknown inputs remain visible. Causal strength is
independent: `arithmetic`, `association`, `supported_effect`, or `unknown`.
Strong evidence for a query does not establish a causal effect.

[Context Graph](../backend/app/modules/intelligence/context_graph.py) derives
authority from validated source records and exposes source kind, authority basis,
validity/freshness, usage, evidence, and contradictions. Published semantic
definitions and reviewed rules outrank usage observations. Conflicting
authoritative records remain visible. Saved text/popularity cannot assign their
own authority.

[Scoped semantic usage](../backend/app/modules/agents/semantic/usage_learning.py)
requires matching principal, role, security version, semantic identity/version,
and fingerprint. Unknown-scope historical rows are excluded. Aggregates contain
bounded workload shapes rather than raw questions, SQL, or filter values.
Usage-informed proposals retain deduplication, preview, regression, review, and
publication through existing owners; learning never automatically publishes a
definition.

## Investigations, scenarios, Actions, and Outcomes

Chat investigation uses existing observation/News lineage. An operation creates
a disabled one-off comparison monitor, gathers authorized observations for
explicit current/baseline windows, and invokes the Investigation service. It
creates no recurring schedule. Missing inputs need clarification; insufficient
observations yield an incomplete result.

The code-owned [Scenario Registry](../backend/app/modules/intelligence/scenarios.py)
registers `unit-economics` version `1` using the existing numerical executor. Its
bounded schema describes shared/option inputs, units, and constraints. Legacy
payloads remain supported. Simulated changes depend on stated assumptions and
retain unknown causal status.

Actions and Action Events extend the Intelligence revision repository. The ledger
records request/decision digests, operation identity, policy/approval references,
dispatch fence, attempt counts, receipts, verification, and compensation. The
current production adapter is [monitor-v1](../backend/app/modules/intelligence/monitor_action.py).

Preview validates the selected Decision revision and governed monitor scope.
Execution requires current policy, authorized reviewer approval, and separate
allow-once consent through the shared tool gate. Smart participants cannot execute
business actions. Durable intent precedes mutation; lease/fence checks protect
writes. Same key/same input recovers the Action; changed input returns `409`.
Uncertain dispatch becomes `verification_required`, or `compensation_required`
for compensation, and cannot automatically dispatch again. Cancellation before
dispatch prevents mutation; after dispatch, recovery uses verification/review.

Verification reads back the governed monitor and task schedule, including owner,
role, handler scope, cadence, and enabled state. It establishes that monitoring
is configured. It does not establish that a business intervention occurred or
caused a revenue change. Compensation is another supervised, consented operation
that disables the monitor/schedule and verifies readback.

Preview performs evidence queries before its short publication lease, then
rechecks the Decision digest, policy, session, and any concurrent preview.
Mutation, review, and verification guards validate current governed evidence and
share grants again. Action coordination uses a bounded 120-second lease section;
other metadata writes retain their 20-second default. Adapter dispatch has a
60-second bound and readback a 30-second bound, within the existing 120-second
HTTP deadline. An expired deadline cannot authorize another dispatch. Cancelled
metadata queries discard their MySQL connection before cursor cleanup so an
interrupted response cannot be reused by another operation. The pinned driver's
connection state is invalidated explicitly, and waiting pool borrowers are
notified after release.

Outcomes link Actions and learning references while retaining observation-window,
completeness, semantic/currency, and causal-attribution guards. Complete observed
data, verified setup, and a causally supported effect are separate conclusions.
Learning allocates a Knowledge revision under the existing memory lease, persists
its Outcome link through the revision repository, then writes the Knowledge
journal and compatibility projection. Knowledge pins the final persisted Outcome
revision. Interrupted retries reuse that pair and repair a missing projection;
unavailable or mismatched references remain hidden until recovery succeeds.

## Analytical workspace

The [workspace contract](../backend/app/modules/assistant/analysis_workspace.py)
provides capability, execution, cancellation, granted-resource inputs, and bounded
outputs. The default executor is `UnavailableAnalysisExecutor`; HTTP execution
returns `503` with an explicit unavailable result. The existing thread-based ML
executor does not provide sandbox isolation.

Default bounds are 30 seconds wall time, 256 MiB memory, 10 CPU seconds, 4 MiB
input, 64 KiB output, 1,000 rows, and three artifacts. Requests prohibit network
access and allow only declared `numpy`/`pandas` packages. These are interface
limits; a future isolated adapter must enforce process/container resource
controls, network/filesystem isolation, cancellation, cleanup, and credential-free
input/output handling before production activation.

## API ownership and acceptance

All paths below are beneath `/api/v1`; caller scope is derived server-side.

| Prefix | Contracts |
| --- | --- |
| `/agents/{agent_id}/versions` | Drafts, guarded publish, manifest inspection/preparation |
| `/agents/{agent_id}/quality` | Cases, runs/results, comparison, monitoring, Doctor, proposal review |
| `/agents/studio` | Thread Missions/resources, grants, run/object links, cancellation, deliverables |
| `/agents/studio/analysis-workspace` | Capability, execution, cancellation |
| `/intelligence` | Chat investigation, scenarios/simulation, Action preview/review/execute/verify/compensate/cancel, existing Decision/Outcome lifecycle |

The [operator guide](governed-studio-operations.md) covers additive bootstrap,
legacy compatibility, recovery, and separate metadata/security acceptance.
Deterministic fixtures, an unpatched engine pass, or clean patch application
alone cannot establish patched-FE enforcement, complete workflow acceptance,
live-provider quality, or production readiness.
