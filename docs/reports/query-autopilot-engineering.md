# Query Autopilot engineering report

Status: code delivery authorized on 2026-10-03 by the user's explicit request to
commit all code and push. Required live acceptance remains incomplete. SQL
frontend hardening and global default-model configuration are separate dependency
commits; Intelligence is committed separately before integration with Autopilot.
The user also authorized merging the worktree into `main`. This delivery exception
does not mark failed or incomplete acceptance as passing.

## Architecture

The backend `query_autopilot` domain implements observation, aggregation,
baselines, diagnosis, scoped evidence, experiments, approvals, application,
verification and compensation. QueryService, the SQL frontend, authenticated
StarRocks connections, audit, managed storage, scheduler and nova-worker remain
the execution owners. The shared provider interface supplies the independent
judge. See [architecture](../arch-14-query-autopilot.md) and
[runbook](../30-query-autopilot.md).

Each workload has one Nova execution UUID, its existing audit UUIDs and optional
engine query IDs. Correlation occurs after consuming results on the same
connection. Streaming, truncation, cancellation, composite execution and proxy
session semantics have regression coverage. A bounded queue persists batches;
telemetry failure can lose observations but cannot repeat or fail user SQL.

Canonical shapes come from the central ANTLR frontend. Fingerprint v2 retains
input-column names when aliases could otherwise collapse distinct predicates.
Performance cohorts separately bind principal, active role, security version,
catalog/database, available policy revision and settings. Replay samples are
opt-in, encrypted and limited to eligible registered statements.

## Files changed

| Area | Implementation |
| --- | --- |
| Backend | New domain; QueryService/proxy correlation; optional audit metadata; scheduler/worker dispatch; model configuration helper |
| SQL frontend | Fingerprint, typed snapshot mapping, aggregate/join MV definitions and optional engine capabilities/syntax |
| Frontend | `/monitoring/autopilot`, scoped API/cache, Overview, Families, Regressions, Opportunities, Experiments and Activity |
| Tests | Domain and consumer regressions, seeded integration, governed fixtures, deterministic retail generator, protocol personas and scorecards |
| Documentation | Architecture, operations, scoped guidance, JSON/Markdown evidence and this report |

SQL frontend hardening is preserved through dependency commit `6f897d8`.
Persistent default-model configuration is dependency commit `7d409b6`. Delivery
integration retains private SQL credential slots, complete rollback metadata,
outer-join nullability, configuration-scoped capability caching, and the assistant
confirmation layout from the latest hardening commit.

## Database changes

Thirteen flat additive `NOVA_SYSTEM.QUERY_AUTOPILOT_*` tables hold `FAMILIES`,
`OBSERVATIONS`, `ROLLUPS`, `BASELINES`, `EVIDENCE`, `INCIDENTS`, `OPPORTUNITIES`,
`EXPERIMENTS`, `ACTIONS`, `OUTCOMES`, `POLICIES`, `ENROLLMENTS` and `JOBS`.
Initialization and upgrade are idempotent. `AUDIT_LOG` gains optional execution
UUID, engine query IDs and execution purpose while retaining its audit UUID.

Observations expire after seven days; replay/profile/log payloads after 24
hours; rollups and baseline history after 90 days. Decisions, approvals,
experiment summaries and outcomes survive automatic cleanup. Managed storage
owns encrypted payloads; Redis remains transport, cache and lease storage.

## Telemetry sources

HTTP and MySQL workloads, selective profiles, ANALYZE PROFILE, structured EXPLAIN,
statistics metadata, resource-group metrics and query-correlated BE logs are
implemented. Configured local log fixtures are separate evidence. Availability
can be unavailable, expired, unsupported or unauthorized. Timestamp proximity
cannot establish a cause. Profile admission pending time is distinct from
pipeline waits, and queue/execution pairs must refer to the same execution.

## Detectors and baseline

The twelve deterministic detectors are absolute latency, sustained percentile
regression, high frequency, cumulative resource cost, plan regression,
cardinality error, statistics, scan amplification, operator skew, contention,
MV opportunities and rare slow queries. Priority exposes impact, frequency,
degradation, expected gain, confidence, cost and risk contributions. Historical
outcomes inform transparent empirical gain estimates.

Mergeable distributions retain counts; percentiles are never averaged. The
rolling 30-minute window compares with 14-day history. Regression requires at
least 100 historical executions in three complete windows, 20 current executions,
a sustained P95 increase of at least 50% and 100 ms, and deviation beyond
historical variation. P99 needs 100 samples. Seasonal comparison needs four
weeks of sufficient weekday/hour support.

The retained natural-window detector case has historical P95 37.039 ms and
current P95 850.925 ms, with 120 historical and 60 recent executions. Unsustained
and 19-current-sample variants are rejected. These earlier experiment-purpose
measurements do not prove durable workload incident detection. The separate
collected-history acceptance is running against actual stored completion times;
its earlier timeout attempts and restored settings remain recorded. Attempt 4
persisted all 180 identities, with zero drops or failed batches, and created a
contention incident. Its current P95 4,181.30 ms was below the actual historical
variation envelope of 4,259.98 ms, so no durable latency regression was claimed.
The next controlled fixture uses a nine-second admission blocker within the
unchanged 15-second operation timeout. Detector thresholds are unchanged.

[Attempt 5](../benchmarks/query-autopilot-2026-10-02/collected-pipeline-attempt-5.json)
revalidates all 180 stored executions and their exact query IDs. Its process was
lost after collection and configuration restoration. Delayed evaluation at the
actual completed rolling window records a matching durable regression: current
30 executions, P95 9,176.98 ms; history 184 executions in seven windows, P95
4,058.33 ms; variation envelope 5,183.28 ms. Observation timestamps and the
history of earlier failures are unchanged. Missing collector process counters
keep the overall run INCONCLUSIVE. This catch-up result is not a live PASS.

Checkpoints now retain accepted/persisted, dropped, failed-batch and queue counts.
Benchmark acceptance also requires a current regression finding and an incident
bound to the current family, security cohort and window. An old incident cannot
satisfy a later acceptance run. Completed restored checkpoints remain eligible
for history reuse after their stored observations and snapshot are revalidated.

[Attempt 6 final validation](../benchmarks/query-autopilot-2026-10-02/collected-pipeline-attempt-6-validation.json)
rejects the older running benchmark's raw PASS. All 180 tracked executions are
persisted with zero drops and failed batches, but its current window has only
absolute latency and contention findings. Current P95 9,161.92 ms against
historical P95 9,086.12 ms is not a new 50% regression. Its earlier incident
cannot satisfy the current acceptance gate. A new isolated generated fixture
cohort stopped with `history_window_expired`; previous measurements remain retained.

The final runner `nova-autopilot-pipeline9-acceptance` used the unit56 source
against `autopilot_snapshot_fresh_20261003`, generated from the nine-table CI
fixture. It stopped after a window expired and restored queue configuration.
Output and checkpoints are in
`/tmp/nova-autopilot-pipeline9`. It uses actual wall-clock observations; no
observation timestamp is changed. Initial collection reserves ten minutes of
window headroom, and comparison waits for the final second-batch sample as well
as the completed previous window. Interrupted fixture runs restored their queue
configuration; their normal observations remain in history. Four unit failures
exposed a recursive window-anchor edit; the corrected complete suite passes
5,059 tests. This live attempt failed; full acceptance remains incomplete.



## Optimization actions and autonomy

Templates support basic statistics, histograms, single-table aggregate and simple
inner-equijoin MVs, refresh policies, plan baselines, resource-group properties,
supported indexes and explicitly enrolled native feedback. Unsupported engine
capabilities remain blocked. Disposable resource trials inherit and verify
sandbox limits and cannot modify the enrolled source group.

| Class | Behavior |
| --- | --- |
| AUTO | Enrolled bounded statistics after a passing trial; native feedback only with explicit enrollment |
| APPROVAL | Histogram, MV/refresh, plan baseline, resource group and index |
| RECOMMEND_ONLY | Partition, sort key, bucketing and application SQL |
| PROHIBITED | Production data destruction, destructive schema changes, security policy and ACCOUNTADMIN modification |

GOVERNED is the initial mode. OBSERVE prohibits application; AUTONOMOUS does not
bypass the action matrix. Administrative mutations require active ACCOUNTADMIN
and applicable engine authorization. Approval binds candidate version, evidence,
experiment, source definition, enrollment and policy. A material change invalidates
approval. Intent precedes side effects; uncertain non-idempotent submission is
reconciled rather than retried. Compensation requires proven Autopilot ownership
and reversibility. Statistics refresh has no claimed rollback.

## Unit test results

The latest complete backend run passes **5,059 tests**, with three warnings,
**55%** application coverage and a duration of **129.88 seconds**. Changed-file
Ruff passes all **148** changed Python files. Agent eval passes **48/48 scenarios**
and **165/165 checks**; the user-data system-access boundary check passes.
The SHA-256 Python manifest matches that run. Full-tree Ruff/mypy retain their
report-only baseline; asyncmy typing stubs remain unavailable.

Coverage includes fingerprints, scope separation, distributions, all detectors,
diagnosis, scoring, typed correctness, policy, transitions, redaction, retention,
concurrency, uncertain side effects, lease loss and crash recovery. The final
suite also checks that restored history can be reused and that old or unrelated
incidents cannot satisfy current-window benchmark acceptance.

## Existing regression results

Query, SQL frontend, proxy, audit, active-role, resource-group and assistant
regressions are included in the full suites. Grammar pin/drift passes and all
eight grammar/artifact files regenerate byte-identically. Generated ANTLR
whitespace is preserved; it is not hand-edited to satisfy a diff whitespace check.

Frontend lint passes with zero errors and 25 warnings, build passes, and coverage
passes **950 tests in 121 files** in **54.94 seconds**. Loaded UI checks pass both
themes, ACCOUNTADMIN controls, keyboard navigation, 390 px width and settled
assistant-open layout. These are local results, not verified GitHub Actions runs.

## Engine integration results

The latest complete seeded native suite passes **169 tests**, with **8 skipped**,
**29 deselected**, one warning and a duration of **393.01 seconds**. The isolated
MinIO seed now honors its configured test port. The application fixture creates
and copies its own real snapshot before checking snapshot provenance. Fresh and
upgrade schema, query-ID correlation, profiles, statistics/plans, owned cleanup,
resource trials, application and verification-window boundaries are covered.

Skips name opt-in custom-tool/cross-cluster/shared-data/Ranger fixtures and the
pin's disabled UDF/unsupported CREATE TASK capabilities. Patched-FE acceptance
runs separately; a native suite cannot establish Ranger enforcement. The complete
integration run passes after the final reviewer projection. Subsequent changes
only harden benchmark acceptance and checkpoint retention.

Ranger acceptance passes active-role isolation, row filters, masks, root refusal,
policy-epoch matching and stale-epoch blocking. The retained MV acceptance covers
five cases: filtered aggregate, masked aggregate, a missing filter column,
changed VARCHAR metadata, and a join retaining a hidden grouping key. Actual
owned governed cycles additionally bind their complete equivalence proof to the
exact enrollment and candidate; separate fixture proof cannot authorize production.

## MySQL end-to-end results and workload

Four personas (executive, finance, marketing and operations) activate roles,
select databases, vary ten parameters and drill into results through Nova's
MySQL protocol. Four containerized MySQL CLI probes pass. The latest
[fingerprint-v2 protocol report](../benchmarks/query-autopilot-2026-10-01/protocol-fingerprint-v2.json)
persists **544/544 observations**, with **zero drops** and **zero failed batches**.
Paired measurement cycles distinguish collection-disabled and collection-enabled
query/drilldown round trips. Parameterized-family and distinct-predicate checks
are separate assertions.

## Dataset

The deterministic local scale contains customers 10,000; product categories 20;
products 1,000; stores 30; orders 50,000; order items 150,000; payments 50,000;
campaigns 24; campaign attribution 10,000. Distribution includes skew, correlated
baskets, seasonality, growth and campaign effects. CI, local, medium and large
scales are available; CI uses 100 customers, 500 orders and 1,500 order items.
The isolated governed success fixture has one million sales rows and 200 customers.
No production database was copied automatically or granted broader access.

## Detected issues and experiments

Scenarios A-I cover frequency/priority, rare-slow control, parameter grouping,
natural-window regression, planted cardinality/statistics evidence, MV discovery,
controlled contention, changed-result rejection and explicitly correlated log
fixtures. Controlled logs and synthetic clocks cannot enter live accuracy
denominators. The corrected CI statistics case crosses the unchanged 1,000-row
support floor: estimated 1 versus actual 1,500 rows. ANALYZE removes both findings;
its noisy latency experiment remains NO_IMPROVEMENT.

The current native MV trial uses three warm-ups and 30 repeats per phase, full
typed/multiplicity comparison, identical snapshot state, controls, actual rewrite
and freshness. It remains INCONCLUSIVE without its own scoped Ranger proof.
Mean latency 311.71 to 328.47 ms does not establish benefit.

| Governed fixture | Trial | Application/verification | Result |
| --- | --- | --- | --- |
| Attempt 15, 50,000 rows | REGRESSED | None | Rejected; owned trial removed |
| Attempt 16, 250,000 rows | SUCCESS | APPLIED, then REGRESSED | Untargeted control regressed; owned MV removal verified |
| Attempt 17, one million rows, 30 repeats | NO_IMPROVEMENT | None | Raw 33.88% mean improvement was smaller than uncertainty; no application |
| Attempt 19, one million rows, 100 repeats | SUCCESS | APPLIED, then SUCCESS | Scoped Ranger proof, exact approval and real 30-minute verification |

## Applied optimizations and after benchmark

[Attempt 19](../benchmarks/query-autopilot-2026-10-02/governed-cycle-attempt-19.json)
applies one approved owned fixture MV. The verification mean falls from **142.80
to 115.45 ms**, with a **14.01 ms** uncertainty margin and repeatable gain. Owner
metadata, freshness, exact scan binding, target/control samples and plan/profile
evidence are available. Metadata inspection uses the exact approving ACCOUNTADMIN
session; workload and EXPLAIN retain the original principal. Missing or changed
owner identity yields an inconclusive outcome.

[Attempt 16](../benchmarks/query-autopilot-2026-10-02/governed-cycle-attempt-16.json)
records REGRESSED and verified compensation. Result-changing and metadata-changing
candidates are rejected. Recorded production actions, unauthorized/prohibited
auto-actions and accepted result-changing optimizations are each **zero**.
These counts describe the recorded cases, not all possible production workloads.

Collection overhead in the v2 protocol run is **43.721 ms (18.57%)** per paired
query/drilldown round trip and **2.244 ms** of additional Nova process CPU. Logical
observation JSON grows **585,746 bytes** for 544 rows. Authenticated SHOW DATA
reports **608.657 to 765.809 KB**, a **157.152 KB** index-size change with the
matching row delta. Shared-host timing, periodic engine reporting, compaction
and retained versions limit attribution; filesystem-wide growth is not measured.

## Accuracy

The offline 16-case scorecard has **13 TP, 0 FP, 0 FN**: precision/recall 100%;
RCA top-1/top-3 each **5/5**. These are deterministic fixtures.
The [updated measured scorecard](../benchmarks/query-autopilot-2026-10-02/live-scorecard-current.json)
has **10 measured cases**, **12 TP, 0 FP, 0 FN**, precision/recall **100%**, and
supported RCA top-1/top-3 each **3/3**. The added D diagnosis uses query-bound
queue/slot evidence. Small denominators cover only the declared labels.
Recommendation and risk classification have deterministic policy/negative tests
and governed acceptance; a broad live accuracy percentage is not established.

## LLM-as-a-Judge

The authenticated primary API persists Kenari `deepseek-v4-1-flash` as global,
light and heavy defaults; actual provider resolution matches. Explicit user/agent
selection, decision and embedding models remain intact. Completion, streaming,
tool calling and structured judge smoke probes all pass. Credentials stay in the
existing encrypted registry. `bash dev.sh` is running; the primary FE is healthy
with its original image, mounts and unlimited memory/swap settings restored.

The latest completed [measured judge run](../benchmarks/query-autopilot-2026-10-02/measured-judge-attempt-13.json)
has **14/14 valid cases**, mean **3.78571/5** and **zero critical hallucinations**.
It **fails** the unchanged rubric-2 target of 4.2/5. Attempt 11 had a provider
schema error and two critical hallucinations; its report is retained. Attempt 12 had 13 valid cases, mean 3.43269/5 and no critical
hallucinations.
Criticism concerns thin early-stage evidence, missing profiles, confidence and
unassessed experiments. The corrected projection distinguishes elapsed and
engine timings, uncertainty, scoped approval, isolated fixture outcomes and the
reviewer's lack of execution authority. The evaluated projection retains bounded
correlated profile pairs and the actual durable detection summary. Missing stages
remain explicitly unassessed.
Failed cases are never dropped, rerubriced or relabeled as passes.

## Remaining limitations and delivery gates

- Finish the persisted collected-history regression trajectory and inspect its
  actual baseline and durable incident.
- Meet the stated DeepSeek score target. The final reduced evidence was evaluated;
  its 3.78571/5 mean remains below the required 4.2/5.
- Finish final diff/secret/reference review and dependency/task commit separation.
  Production code matches the complete seeded integration run; final benchmark
  acceptance and checkpoint changes pass the full unit suite.
- Fetch/integrate remote main before every commit and again before push, then
  push the task and merge into main under the explicit delivery exception.

Engine capability opt-ins and unattributed filesystem growth remain explicit.
The user explicitly authorized commit/push despite the disclosed open live gates.
Acceptance completion remains blocked. Retained attempts document failures; they
are not passing evidence. Runner9 restored configuration after collecting 80
observations with zero drops and failed batches, then reported `history_window_expired`.

## Code delivery on 2026-10-03

The user explicitly requested committing all code and pushing despite the
previously disclosed live acceptance limitations. SQL hardening `6f897d8`,
default LLM configuration `7d409b6`, Intelligence `3843ff1`, and Query Autopilot
`3a0a5be` are separate commits. Both feature branches have been pushed.

The integrated source passes 5,243 backend unit tests with 55% coverage, 270 eval
trajectories, the 48/48 scenario and 165/165 check scorecard, changed-file Ruff,
the user-data boundary checker, grammar pin/drift and byte-identical regeneration.
Frontend lint and build pass; all 970 tests in 125 files pass with 46.13% line
coverage. One earlier component run failed with an unstable locator; the full
integrated rerun passes. Seeded real-engine integration is still running.

The integration preserves both domains, resolves only a provider-test formatting
conflict, and excludes generated coverage files. No stored deployment credentials
are included. Source hashes confirm the tested code and configuration have not
changed during validation. Live Query Autopilot acceptance remains incomplete;
the explicit delivery exception does not count failed attempts as passing.
