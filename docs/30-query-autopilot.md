# Query Autopilot operations

Use [HOW_TO_RUN](../HOW_TO_RUN.md) for Nova's authenticated engine, Redis,
managed storage, scheduler and worker setup. The feature requires the normal
QueryService path and named active roles. It does not need another database or
inference loop. Review [the architecture](arch-14-query-autopilot.md) and current
[acceptance status](reports/query-autopilot-engineering.md) before enabling
production applications.

## Configure DeepSeek

Kenari must already contain the active registered LLM `deepseek-v4-1-flash` and
its encrypted credential. Use an existing Nova session with active ACCOUNTADMIN.
Keep its token in the shell environment, never in a command argument or report:

```bash
cd backend
uv run python scripts/configure_autopilot_model.py --url http://127.0.0.1:8000
```

The command reads `NOVA_ACCESS_TOKEN`, validates the registry, saves the global
selection through `/api/v1/ai/default-model`, updates only light/heavy model IDs
through decision settings, and reads both settings back. It preserves the
decision model, thresholds, timeout and enabled state. Embeddings and explicit
agent/user model choices remain under their existing owners. The two API writes
are separate: a partial result is an error and rerunning reconciles it.

To explicitly test the stored provider credential, completion, streaming, tool
calling and structured judge output against the configured backend environment:

```bash
uv run python -m tests.benchmark.query_autopilot.report \
  --live-llm --output artifacts/query-autopilot
```

This command makes live provider calls. Provider or connectivity errors fail the
live report. Without `--live-llm`, the command is deterministic and offline and
marks inference SKIPPED. Fixture judge scores do not establish production RCA
accuracy.

## Enrollment

In Monitoring > Autopilot, activate ACCOUNTADMIN, choose a family, and register
an existing snapshot database. Supply a complete mapping for the query and its
control workload, snapshot provenance, the dedicated replay principal/role,
and a bounded resource group. The replay identity must already have privileges
on its sandbox objects. In native-RBAC fixtures, materialized views have their
own REFRESH/SELECT/ALTER/DROP grants; table grants do not imply these privileges.

Replay sampling is opt-in per exact cohort. Turning it off or changing the
enrollment version invalidates pending uploads and candidate validation.
Default mode is GOVERNED. Start with OBSERVE when collecting a baseline without
any experiment/application work. Every experiment needs a control family and
an immutable snapshot, three warm-ups and at least 30 measured repeats.

To inspect a blocked job, use Activity and the candidate's evidence/experiment
view. Missing identity, expired sample, unsupported engine capability, resource
measurement failure and stale approval are separate blockers. A changed
action definition also blocks apply when it differs from the statements bound
to the successful experiment; rerun validation before seeking a new approval.
An uncertain application must be reconciled from engine state; do not resubmit it manually
under a fresh idempotency key without investigating the original intent.
Verification waits 30 minutes after application completion and object readiness.
The interval spent applying the change is excluded from both comparison windows.
Missing completion metadata blocks verification; intent time cannot substitute
for a confirmed application timestamp.

Verification reads MV ownership and freshness metadata through the approving
ACCOUNTADMIN session bound to the application. It revalidates that session,
role and context version. Workload queries and EXPLAIN continue to use the
enrolled workload identity. An expired or changed approval identity leaves
metadata unauthorized and cannot produce a SUCCESS outcome.

## Isolated retail acceptance

Use the separate native test stack from HOW_TO_RUN and CI. Override all
`NOVA_TEST_*` published ports and `COMPOSE_PROJECT_NAME` consistently for pytest;
setting only application `STARROCKS_FE_MYSQL_PORT` does not redirect its fixture.
The native stack cannot establish patched-FE Ranger acceptance.

After seeding the isolated stack, explicitly create a fresh fixture:

```bash
NOVA_AUTOPILOT_FIXTURE_STACK=1 uv run python -m tests.benchmark.query_autopilot.load_retail \
  --database autopilot_retail_trial --sandbox autopilot_snapshot_trial \
  --scale local --directory /tmp/nova-autopilot-retail
NOVA_AUTOPILOT_FIXTURE_STACK=1 uv run python -m tests.benchmark.query_autopilot.protocol \
  --database autopilot_retail_trial --output artifacts/query-autopilot
NOVA_AUTOPILOT_FIXTURE_STACK=1 uv run python -m tests.benchmark.query_autopilot.experiment_live \
  --manifest /tmp/nova-autopilot-retail/manifest.json --output artifacts/query-autopilot
NOVA_AUTOPILOT_FIXTURE_STACK=1 uv run python -m tests.benchmark.query_autopilot.scenarios_live \
  --database autopilot_retail_trial --contention-database autopilot_snapshot_trial \
  --wall-clock-history --output artifacts/query-autopilot
```

Supply the isolated application endpoint variables and throwaway fixture signing
and Fernet keys documented in CI. The loader refuses existing database names
and uses `autopilot_` names. It never loads production. Scales are CI, local,
medium and large; local has 10,000 customers, 50,000 orders and 150,000 order items
across nine deterministic tables.

The protocol report records four actual proxy personas, containerized MySQL
CLI checks, disabled/enabled collection timings and logical observation growth.
Run performance measurements without competing acceptance jobs where possible;
record shared-host contention when interpreting them. The native MV trial uses
the dedicated replay identity, persists an experiment, proves full-result
comparison, and removes its fixture MV when authorized. It must stay
INCONCLUSIVE without separate Ranger proof. A native safety-gate pass is not a
production optimization success.

The scenario runner plants statistics/cardinality and admission-contention
conditions only on the selected fixture. It restores temporary queue settings.
Use the registered snapshot for contention: the loader gives its replay identity
access to that database, while the four workload personas use the retail fixture.
The runner never grants missing privileges. An unavailable contention phase stays
UNAVAILABLE in the report and cannot pass complete acceptance.
`--wall-clock-history` collects three complete 30-minute historical windows and
two recent windows, taking up to two hours. Without it, real query measurements
feed a controlled detector clock; the report explicitly leaves wall-clock
history acceptance unverified and the command does not claim complete acceptance.
Run this command without other work against that test cluster. Its atomic
`contention-progress.json` records configuration before mutation and measurements
as they complete. A restart invalidates interrupted measurements; the runner
reconnects for configuration restoration and records restoration failure
explicitly. Check that record before another fixture run. The native test
Compose caps the FE heap at 1 GiB for the local 8 GiB Docker budget.
Use fixed FE/BE addresses and persistent engine volumes for the entire run.
The checkpoint binds each batch to the partition IDs and visible versions.
Changed data or an incomplete historical window invalidates collection. Check
Docker disk space too: the pinned FE's BDB reserve can reject writes before
the disk is full. The durable collection command also checks observations and engine IDs in
`NOVA_SYSTEM` before inspecting persisted baseline and incident records:

```bash
NOVA_AUTOPILOT_FIXTURE_STACK=1 uv run python -m tests.benchmark.query_autopilot.pipeline_live \
  --database autopilot_snapshot_trial --output artifacts/query-autopilot-pipeline
```

If collection missed a natural window after all 120 historical measurements
completed, resume into a new output directory with
`--resume-history artifacts/query-autopilot-pipeline/pipeline-progress.json`.
Checkpoints retain collector counters alongside execution IDs. Durable acceptance
requires a current regression finding and an incident in the same family, cohort
and window; an older incident cannot satisfy the check. Repeated slow fixture
runs contribute to the historical baseline and may cease to represent a new
regression. Retain those measurements rather than deleting them to obtain a pass.
Initial collection selects a window with at least ten minutes of headroom. The
rolling comparison waits until both the previous window and the last measured
sample of the second batch are complete. Actual completion timestamps remain
unchanged when a batch finishes later than expected.
The runner verifies the retained execution IDs, timestamps, family/cohort,
result proof and current snapshot before collecting two new recent windows.
It refuses incomplete or expired history and unverified configuration
restoration. Existing timestamps stay unchanged; gaps between historical and
recent windows remain visible in the report. Earlier partial recent measurements
remain persisted and are excluded only from the resumed run's 180 tracked
executions. Aggregation still includes every eligible observation in the exact
security cohort, including failed-run measurements. Use `--blocker-seconds 4`
for a stronger controlled admission delay when testing further degradation;
the permitted range is one to ten seconds, within the unchanged 15-second query
and operation timeout, and the report records the duration. A larger controlled
regression can also cross the unchanged absolute-slow threshold; that additional
finding is retained rather than suppressed.
This does not alter the baseline or detector thresholds.

For a complete governed fixture cycle, run the following inside the isolated
patched-FE backend with its documented fixture credentials already in the
environment:

```bash
NOVA_AUTOPILOT_FIXTURE_STACK=1 python scripts/verify_autopilot_governed_cycle.py \
  --sales 50000 --customers 200 --output /tmp/autopilot-governed-cycle
```

This command creates owned source/snapshot tables, a bounded builder role and
matching Ranger fixture policies. It uses the existing services for setup,
then requires a measured trial and bound approval before application. It waits
for a real 30-minute verification window. Add `--post-apply-contention` for the
negative trajectory: the fixture fills its admission queue only for target
executions after application. It restores its queue and resource-group settings;
passing requires REGRESSED and verified removal of the owned MV. Run these
fixtures sequentially on a memory-limited Docker host. The runner retains failed
verdicts and does not seed a successful experiment or alter timestamps. Keep
other acceptance workloads off this fixture cluster and retain its durable
progress record if interrupted.

For the separate isolated patched-FE fixture, set
`NOVA_AUTOPILOT_FIXTURE_STACK=1` and supply its administrator password through
`NOVA_ADMIN_TEST_PASSWORD`, then run `scripts/verify_autopilot_ranger.py --output
artifacts/query-autopilot`. Its cases exercise two principals, row filters,
masking, safe join rewrite, denied rewrite and typed-result rejection. The
script removes its own objects and never issues proof for an unrelated
production enrollment or snapshot. Run `scripts/verify_ranger_e2e.py` as well
for proxy, active-role, masking, agent-tool and root-guard regressions.

Operator-only local log fixtures can be configured with
`QUERY_AUTOPILOT_LOCAL_LOG_FIXTURES`, a JSON mapping of exact cohort IDs to
explicit file paths. Reads are bounded to the last MiB per file and 200 matching
lines, verify the live role, and select only the requested query UUID. File paths
are never accepted from the API. Leave this setting empty outside fixture work.

## Required delivery checks

Follow root and scoped AGENTS.md plus CI. Run backend unit coverage, agent eval,
changed-file Ruff, seeded real-engine integration, security boundary checks,
grammar drift/regeneration, frontend lint/build/coverage and layout checks.
Patched-FE row-filter/masking acceptance is separate. Do not infer it from
native SQL grants or mocked tests. Collect JSON and Markdown reports, including
case counts, failures and unavailable evidence, before commit and push.

## Retained engine evidence and live judge

Ranger control-plane policy/tag revisions participate in the cohort. A policy
change requires a new enrollment version and fresh evidence/acceptance; an old
approval cannot authorize the new scope. The revision check does not replace FE
propagation checks.

A measured review uses the existing registry and shared provider interface. Run
it explicitly against retained reports and the configured Nova control plane:

```bash
cd backend
uv run python -m tests.benchmark.query_autopilot.measured_judge --live-llm \
  --scenarios /path/to/scenarios-native.json \
  --contention /path/to/contention.json --regression /path/to/regression.json \
  --experiment /path/to/experiment-native.json \
  --pipeline /path/to/collected-pipeline.json \
  --output /path/to/measured-review
```

The command reduces inputs to finite numeric measurements, closed diagnosis
categories, detector identity and deterministic guidance. It retains verified
operator row pairs, up to 30 numeric resource samples per trial phase and closed
experiment failure reasons. Before/after plan structure retains up to 100 closed
operator labels and finite estimates per phase. SQL, logs, stored credentials and query
identifiers do not enter provider context. Controlled log fixtures and simulated
historical clocks are excluded. Provider/schema errors fail the run. Its report
states case counts, average score and critical hallucinations; it cannot apply
an action.

The projection distinguishes selectively sampled engine profiles from Nova
elapsed wall-clock summaries. It retains at most 100 finite query-correlated
queue/execution pairs, explains mean differences against their uncertainty,
and discloses unassessed stages. A governed review checks candidate and durable
application bindings before retaining an administrative approval summary. Fixture
outcomes carry their evaluation scope; the reviewer's lack of execution authority
does not erase an application already recorded by the governed worker.

Rubric version 2 requires eight scores: detection, evidence, diagnosis,
recommendations, safety, experiment validity, outcome interpretation and
explanation. The target remains at least 4.2/5 across all requested cases with
zero critical hallucinations. The optional pipeline input adds a separate
D-durable review of the actual scoped family baseline, findings and current
profiles, including failed-run history; the retained detector-only D case stays
separate. A passing older rubric does not satisfy this gate.
Each judge call has a 90-second bound and one shared-provider attempt. A registry
initialization failure produces a failed report without calling the provider.
Use the configured control-plane endpoint and its existing encryption settings;
throwaway fixture keys cannot decrypt the live provider registry.

To increase statistical power after an equivalent but inconclusive fixture
trial, use `--repetitions 100 --reuse-fixture <registered-candidate-id>`. Supply
the same sales/customer counts. The runner reads authoritative fixture,
enrollment and trial records; it refuses applied candidates, uncertain cleanup,
changed provenance or a remaining trial object. It rechecks the original
row-filter/mask and bounded-builder access before a fresh enrollment and trial.
No retained verdict or measurement is reused as a new result.
