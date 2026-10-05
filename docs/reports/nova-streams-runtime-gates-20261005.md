# Nova Streams runtime gates — 2026-10-05

Status: release acceptance is incomplete. Streams admission remains off. This
report records foundation work; it does not establish a production provider,
SQL consumption runtime, Explorer availability, or a successful local rollout.

## Recovery changes

- Dispatch arbitration now uses Stream identity, epoch, and generation. Different
  operations on the same generation cannot acquire separate submission claims.
- The winning digest binds the source, interval, schema version, target, principal,
  and operation. Recovery rejects substituted facts before reading target evidence.
- Verification preserves distinct load-job and transaction IDs. A cancelled load
  requires transaction evidence before it can release a consumption fence.
  `COMMITTED` is distinct from `VISIBLE`; source publication must require visibility.
- Recovery can use an already persisted terminal receipt after engine evidence
  becomes unavailable. Conflicting receipts block recovery.
- `AUDIT_STREAM_CONSUMPTION_OPERATIONS` preserves the complete source interval,
  target identities, principal, active role, security-context version, and decision
  digests before arbitration. A replacement worker reconstructs the winning
  operation from these facts; missing legacy facts block recovery.
- `AUDIT_STREAM_NAMESPACE_OPERATIONS` stores immutable namespace operation facts
  before arbitration. Publication recovery reconstructs the winning record from
  those facts. Hash-only claims without recoverable facts remain blocked.
- The additive recovery migration preserves the shipped `CONFIG_STREAMS` layout
  and includes consumption journal initialization. Runtime initialization, Docker
  initialization, and integration seeding apply the same definitions.

## Engine evidence

Tests used disposable databases on stock StarRocks 4.1.4, ARM64. Docker provided
eight CPUs and 8 GiB of memory, shared with the active local deployment.

| Image | Repository digest |
| --- | --- |
| FE | `sha256:0b9099483e52ee66f9ee0244799bd32f3ccb491492857c59ada622ceb15b914b` |
| BE | `sha256:1cd28b5f3fd81213c78424064a56bfc423d7c0c577a9a6406171938329f9e532` |

The durability suite exercised successful and empty INSERT receipts, a running
INSERT followed by cancellation, concurrent claimants, separate claim processes,
label reuse after expiry, and competing consumption operations. The expiry test
checks that a new load-job ID was allocated while the original winner remained.

A separate FE used `publish_version_interval_ms=30000`. The visibility test
observed `COMMITTED` with zero target rows, then `VISIBLE` with one target row,
using the same transaction identity. This test passed in 65 seconds. The
`NOVA_STREAM_GATE_DELAYED_PUBLICATION=1` switch requires that isolated configuration;
it must not be enabled against the active deployment.

Namespace tests passed fresh/repeated migration, concurrent publication,
claim-before-publication crash recovery, and upgrade of a previously published
legacy row. These tests do not exercise the public SQL execution lifecycle.

Stock engine evidence is not Ranger acceptance.

## Integration investigation

An instrumented Studio integration run completed with a failure after 984.84
seconds: decision lineage returned HTTP 504 `cycle_deadline`. Earlier interrupted
runs are not passes. API requests continued to make progress before that failure.

Source-access probes grouped eight sources behind each semaphore permit. A
change schedules individual probes under the existing four-connection limit;
delegated connections remain serial. Focused tests retain principal, role,
context-version, denial, and cancellation checks. The subsequent full engine run
completed in 2049.42 seconds: 203 passed, 1 failed, 16 skipped, 30 deselected.
The Studio test progressed to outcome evaluation, then returned HTTP 404
`semantic_version_unavailable`. This is still a failed release gate; the source
of that denial requires diagnosis. Skipped governed tests do not establish Ranger
acceptance.

After the complete operation journal was added, focused foundation tests passed
(105 unit tests; 12 engine tests, with two configuration-specific scenarios
skipped). A further engine test passed recovery by a newly constructed worker
using persisted winner facts and terminal receipts while the engine-evidence
reader was deliberately unavailable. Target row count remained one. These
focused checks do not replace a final full-suite run.

A separate real-engine test used a newly created restricted writer with an
explicit active role and INSERT privilege only on the disposable target. Receipt
verification succeeded for that principal and rejected substitutions of the
principal or target. The test passed in 9.19 seconds and removed its user and role.
This verifies receipt identity binding under native RBAC, not Ranger policies or
the future managed-source access adapter.

Before synchronization with `main`, the full backend unit command with branch
coverage passed: 6,797 tests, 59% aggregate coverage, 521.38 seconds. Full
`tests/eval` passed 304 tests; the eval scorecard passed 48/48 scenarios and
165/165 checks. Scoped SQL grammar/namespace/consumer regressions passed 177
tests. Changed-file Ruff, grammar drift, the user-data boundary checker, shell
syntax, `git diff --check`, and test Compose `config --quiet` passed.

An additional instrumented Studio run failed during semantic-view creation:
`_source_access` returned false after 30.136 seconds. This identifies an access
probe failure but does not yet distinguish an engine refusal from a timeout.
The authorization gate was not bypassed or weakened.

A later diagnostic run progressed through view publication and agent access
verification but was interrupted before completion. It is not a passing test
result. The completed full-suite failure above remains the release-gate result.

The stock FE rejects optional UDF bootstrap before the end of `init-nova.sql`.
Integration seeding therefore applies the additive Streams migrations explicitly
and fails if they fail, instead of relying on statements after the optional UDF.

## Deployment boundary and remaining acceptance

Publication checks after integrating `main` at `b789b3c`:

| Command (from `backend/`) | Result |
| --- | --- |
| `uv sync --locked` | Passed |
| `uv run pytest tests/unit --cov=app --cov-branch --cov-report=term-missing --cov-report=xml:/tmp/nova-streams-pr-coverage.xml` | 7,004 passed, 59% aggregate coverage, 591.33 seconds |
| `uv run pytest tests/eval -q` | 304 passed |
| `uv run python -m tests.eval.report` | 48/48 scenarios, 165/165 checks |
| `uv run pytest tests/unit/test_sql_dialect_grammar.py tests/unit/test_stream_namespace.py tests/unit/test_stream_sql_consumers.py tests/unit/test_sql_frontend_consumers.py -q` | 177 passed |
| `NOVA_ORCH_SR_PORT=39030 uv run pytest tests/integration/test_stream_durability_gate.py tests/integration/test_stream_namespace_gate.py -m engine -q -rs` | 13 passed, 2 configuration-specific skips |
| Changed-file Ruff, grammar drift, user-data boundary, seed shell syntax, test Compose configuration, diff checks | Passed |

The full seeded integration suite was not rerun on this integrated tree. Its
earlier failed result remains unresolved, and governed Ranger acceptance remains
outstanding. Publication is a draft foundation milestone, not release acceptance.

At the initial inventory, the active backend, frontend, and proxy were running
from `feat/semantic-view-news` at `f9a796b`, ahead of the initial Streams base.
The publication branch subsequently integrated `main` at `b789b3c`, including
News PR #176. A release candidate must still verify preservation of the active
deployment's work before rollout.
FE/BE HTTP ports remain host-published. No active deployment configuration,
admission flag, source registration, or business table was changed by this work.

Managed source registration and append, authorized SQL peek/consume, task triggers,
retention, API and Explorer, governed acceptance, benchmarks, verified backup and
restore, network fencing, and active deployment smoke remain release gates.
Passing the foundation tests does not satisfy those gates.
