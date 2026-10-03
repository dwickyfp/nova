# Query Autopilot

Status: implementation under acceptance. The engineering report records verified
behavior and remaining delivery gates. Do not enable production applications
based on the offline detector scorecard alone.

Query Autopilot observes the existing QueryService execution boundary. HTTP,
Studio tools, and MySQL proxy workloads keep their authenticated connection,
active role, SQL frontend, guards, and audit path. Diagnostic, experiment, and
maintenance calls carry an execution purpose and cannot recursively become
workload observations.

## Execution identity and telemetry

A Nova execution UUID links one bounded workload observation to its existing
Nova audit UUIDs and optional StarRocks query UUIDs. QueryRepository consumes or
drains the result before reading LAST_QUERY_ID on the same connection. Missing
correlation stays unavailable. The same session read uses the pinned engine's
CATALOG() and DATABASE() functions to capture the execution context. Unknown
engine catalog context cannot reuse a default-catalog enrollment. The proxy preserves the caller's LAST_QUERY_ID
semantics across instrumentation. Cancellation closes an interrupted connection;
collection never resubmits a user statement.

Total Nova latency, engine round-trip latency, fetch time, returned rows, and
truncation are separate fields. Physical engine time requires a profile. A
bounded 2,048-item queue writes batches of at most 128 observations. Queue and
persistence failures increment process-local monitoring counters. They can lose
telemetry and cannot fail or repeat a workload query.

The central ANTLR frontend owns canonicalization, snapshot relation mapping,
read-only replay eligibility, order proofs, and supported MV shapes. Literal
values, comments, whitespace, and local aliases normalize without erasing join,
predicate, grouping, ordering, window, positional-reference, or limit structure.
Unsupported statements remain unclassified and cannot generate executable
structural changes.

Fingerprint version 2 keeps relation and output aliases in separate namespaces.
Relation aliases normalize only as qualified references. Output aliases normalize
in the outer ORDER BY; possible input-column names in WHERE, GROUP BY, HAVING or
window expressions retain their spelling. This prevents distinct predicates from
collapsing into a family. Ambiguous alias resolution still belongs to the normal
binder at execution, and canonical text is never executable SQL. Versioned family
IDs keep earlier observations separate.

A shared query shape is distinct from its performance cohort. A cohort includes
principal, named active role, security-context version, catalog/database,
available policy revision, and query settings. Native observations without a
named active role remain visible but cannot enroll for replay. Two people with
the same role never share a cohort.

In Ranger mode, the existing client supplies the service, policy and tag revision
counters when available. The collector caches this control-plane revision for
five seconds and freezes it at execution start. This is not an assertion about
FE policy propagation. Enrollment records the current revision; delegated
connections and statements recheck it. Changed or newly available revisions
invalidate the old scope; a metadata outage blocks delegated work. Observation
collection remains best effort and cannot interrupt user queries.

## Persistence and scheduling

Flat NOVA_SYSTEM.QUERY_AUTOPILOT_* primary-key tables hold families,
observations, rollups, baselines, evidence, incidents, opportunities,
experiments, actions, outcomes, policies, enrollments, and jobs. Initialization is additive
and idempotent. The existing scheduler persists aggregation and cleanup jobs;
the existing nova-worker consumes durable intents. Redis provides renewable
leases for jobs, candidates, objects, and sandbox databases.

An interrupted non-idempotent action is reconciled against engine state and its
persisted acknowledgement. Unknown outcomes block submission. Compensation also
persists intent before execution and checks exact object ownership. A retry
inspects that intent instead of repeating an uncertain DROP. Completed
verification measurements persist before the candidate's terminal transition so
a crash cannot discard a measured outcome.

Observations default to seven days, encrypted replay/profile/log payloads to
24 hours, and rollups/baselines to 90 days. Managed artifact storage holds
bounded encrypted payloads; metadata stores opaque references and provenance.
References are hidden from API responses. Decision, approval, experiment and
outcome records survive automatic cleanup. Expired evidence referenced by those
records retains its provenance; unreferenced evidence metadata can expire after
the history period. Completed periodic housekeeping jobs expire separately.

## Evidence and deterministic decisions

Selective profiles, ANALYZE PROFILE, EXPLAIN COSTS, statistics metadata,
resource-group metrics, and query-correlated BE logs use the caller's governed
SQL path. Evidence distinguishes unavailable, expired, unsupported, and
unauthorized. Local log fixtures require an operator configuration keyed by the
exact cohort; paths do not enter responses. Log diagnoses require an explicit
engine error on a line naming the exact query UUID. Nearby timestamps alone do
not prove a cause.

The pinned profile adapter reads FE admission pending time separately from
pipeline waits and retains exact row counters when the engine prints both a
rounded and complete value. Counter pairs come from the same execution. A
resource snapshot without a matching query ID cannot establish contention.

Mergeable logarithmic latency distributions retain counts. Percentiles are
recomputed from merged bins. The rolling 30-minute window compares with 14 days
of history, subject to 100 historical samples in three complete windows and 20
current samples. P99 requires 100 samples. Regression requires a sustained P95
increase of at least 50%, at least 100 ms, and a value outside historical
variation. Matched weekday/hour comparison requires four weeks of support.
It matches the hour across both fixed half-hour history windows, so a rolling
window between those boundaries can use the seasonal history. Aggregation also
ages recently active cohorts without new observations; a completed rollup cannot
leave an old frequency finding in the current family summary indefinitely.

The twelve detectors, root-cause rankings, priority contributions, correctness,
risk classification, and transitions are deterministic. Historical outcomes
adjust expected gain transparently. A DeepSeek judge receives a reduced
projection for independent evaluation and has no action or approval tools.
Rubric version 2 scores detection, evidence, diagnosis, recommendations,
risk/approval safety, experiment validity, outcome interpretation and explanation.
Only closed detector/action/verdict vocabulary, query-verified operator row pairs,
bounded numeric resource samples and trial/outcome summaries enter its context.
Before/after plan operators and finite estimates survive redaction through a
closed operator vocabulary; table names, attributes, SQL and hashes are omitted.
Redaction preserves detector identity through repeated projections. Closed
experiment failure reasons distinguish missing acceptance from timing verdicts.
Each review has a 90-second total limit and one provider attempt;
timeouts and provider errors remain failed reviews.

Governed MV acceptance binds the family/cohort, original scoped principal,
security-context version, policy revision, snapshot and replay shape. Unavailable,
expired, unauthorized, unsupported or mismatched proof produces its own explicit
INCONCLUSIVE reason. Engine rewrite and freshness checks run after that proof
passes; measured rewrite alone cannot authorize a governed candidate.
The proof also binds the dedicated replay scope and the actual partition IDs and
visible versions. When no acceptance reference is enrolled, the worker can
collect a proof on that registered snapshot through the original workload
principal and role. It compares supported source/snapshot security effects,
runs three warm-ups and 30 measurements per rewrite phase, and requires full
typed equivalence, exact MV scan binding and unchanged freshness. Unsupported
principal/group/conditional policies block this comparison. The effect preview
does not replace FE authorization or prove policy propagation by itself.
The enrolled or collected Ranger proof also enters the candidate's evidence digest and
availability checks. Replacing, modifying or expiring that proof invalidates
approval/application eligibility without relying only on an enrollment version.

## Experiments and actions

Enrollment names an existing snapshot database on the same cluster, a table
mapping, snapshot provenance, a dedicated execution identity and role, a
resource group, result/time/memory budgets, and an independent control family.
Autopilot does not clone production or grant privileges. Source access is
revalidated during replay. Opt-in encrypted SQL samples expire after at most
24 hours; unsupported, volatile, or credential-bearing statements cannot replay.

Experiments run three warm-ups and at least 30 measured repetitions per phase.
The same parameters and partition-version state must remain stable. Full typed
results preserve multiplicity and deterministic ordering. Truncation, ambiguous
ordering, comparison budgets, authorization changes, or snapshot changes make
the trial inconclusive. Plans, variance, resource samples, and the independent
control accompany measured latency.
Application rechecks that same partition-version state under the dedicated
enrolled identity. Missing snapshot evidence or a changed snapshot blocks
application before maintenance begins. Setup grants use the access-control
service's explicit database, table and materialized-view resource scopes;
select remains a column resource, while creation and maintenance use their
engine-defined resource levels.

Resource-group trials create a disposable group and select it explicitly on the
replay connection. The engine-required classifier matches only `0.0.0.0/32`,
which cannot be a TCP client's source address. The trial copies inspectable CPU,
memory, concurrency and large-query limits from the enrolled sandbox group;
proposed limits cannot exceed that budget. Unavailable CPU limits, exclusive CPU
reservations and alternate warehouses block the trial. Durable ownership and
cleanup checks include the engine group ID, properties and classifiers, so a
replaced or externally modified group is not dropped. The original group is
not altered. Trials require the enrolled sandbox baseline to have the same
inspectable limits as the target production group. Otherwise the trial blocks
rather than attributing a gain to a different baseline. The experiment binds both
engine group identities, properties and classifiers; changing either group
invalidates application before an action intent or DDL. Production resource-group
changes still require exact approval.

Refresh-policy trials clone the exact owned MV's supported query and schedule
onto registered snapshot tables, alter only the disposable clone, and bind the
original engine identity and definition to the experiment. Changed ownership
invalidates application. A redundant refresh reported as SKIPPED is ready only
when the pinned FE confirms a valid rewrite, zero errors, freshness at or after
the last successful refresh, and compatible base-table versions. Missing fields
leave freshness unproven. Refresh trials retain the same scoped Ranger gate as
MV creation.

GOVERNED is the initial mode. Bounded enrolled basic statistics maintenance can
be AUTO after a passing trial. Explicitly enrolled native feedback follows the
same constraints. Histograms, MVs, refresh policies, plan baselines, resource
groups and supported additional indexes require approval. Partition, sort key,
bucketing and application SQL changes are recommendations. Destructive data or
schema operations, security-policy changes and ACCOUNTADMIN modifications are
prohibited. OBSERVE stops execution; AUTONOMOUS retains the action risk matrix.

Approval binds the candidate version, targets, evidence digest, experiment
result, enrollment version, and policy version. A new experiment increments the
candidate version. Changed evidence or expired approval cannot authorize apply.
The experiment also records the digest of the production action statements.
Apply recompiles from the bound sample; a different definition, including a
compiler change between deployments, clears approval and blocks maintenance.
MV acceptance additionally requires actual rewrite, freshness, equivalence and
an exact, unexpired patched-FE Ranger row-filter/masking acceptance record.
MV definitions retain unprojected grouping keys so Ranger can evaluate filters;
the workload projection stays unchanged. Rewrites that change result metadata
fail typed comparison even when cell values match.

Post-apply verification waits for a complete 30-minute window with target and
control samples after SQL completes and the object is ready. The action records
both intent start and application completion. The before window ends at intent
start; execution during application is excluded from latency, plans and profiles.
A missing or ambiguous completion timestamp blocks verification rather than
guessing a window boundary. SUCCESS requires matching production plans/resource evidence,
the configured P95 gain, and a decrease in mean latency exceeding a sampling
uncertainty margin. That margin uses 1.96 standard errors and an upper bound
on sample variance from each histogram bucket's interval. It estimates variation
in the observed workload; it does not establish causality. An apparent percentile
gain unsupported by that comparison stays INCONCLUSIVE. Outcomes retain the
comparison, controls and explicit observational provenance.
MV success additionally requires a current exact MV scan, a ready object and
the unchanged owned-object binding. A candidate name mentioned elsewhere in
EXPLAIN cannot establish a rewrite.

Statistics refresh has no claimed rollback. MV/index/plan-baseline compensation requires an
Autopilot-owned object with an unchanged engine identity and definition.
Sandbox cleanup persists its intent and verifies absence after removal. Crash
recovery revalidates enrollment, original access and ownership; unknown creation
or DROP outcomes never trigger an unverified retry.

## API and monitoring

`/api/v1/query-autopilot` exposes overview, bounded collection/detail reads,
policy and enrollment configuration, and candidate mutations. Administrative
monitoring roles can read across users. Policy, enrollment, approval and apply
require active ACCOUNTADMIN; the engine must still authorize target access.
Mutation requests include candidate version and an idempotency key and return
a durable operation ID.

`/monitoring/autopilot` provides Overview, Families, Regressions, Opportunities,
Experiments and Activity. Query cache keys include the current principal, role,
and security-context version. The detail view distinguishes measured outcomes
from estimates, exposes evidence availability and priority contributions, and
shows structured before/after plan changes and stale approval reasons.

See [the runbook](30-query-autopilot.md) and
[the engineering report](reports/query-autopilot-engineering.md) for commands
and acceptance limitations.
