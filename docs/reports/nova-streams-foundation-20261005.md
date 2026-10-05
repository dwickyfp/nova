# Nova Streams foundation: incomplete implementation

This worktree does not implement the production Nova Streams V1 release. No
Streams routes, SQL statements, source registrations, or feature activation are
installed. Do not deploy these modules as a CDC feature.

Branch: `feat/nova-streams`, initially based on `origin/main` at `f62ff4c`.
The existing checkout and its unrelated work were not edited.

## Implemented and exercised

- Opaque epoch/sequence cursors, append-only/full-CDC capability checks, bounded
  errors, and deterministic append row identities.
- Immutable claim arbitration using a deterministic INSERT label plus durable
  existence guard. The Redis lease is not the durable claim.
- A bounded loading-metadata verifier that checks database, table, label,
  submitting principal, load type, and known load identity. Missing, ambiguous,
  nonterminal, or unavailable evidence remains `VERIFICATION_REQUIRED`.
- One-shot consumption dispatch with competing submit/cancel decisions. Recovery
  does not invoke target submission. A worker suspended before submission cannot
  be cancelled merely because its target label is not visible yet.
- Immutable consumption/offset receipts. Repeating an older recovery does not
  decrease an already committed offset. Load IDs are not represented as
  transaction IDs.
- Immutable Parquet object writes through existing configured storage secrets,
  schema checking, upload/row/decoded-size limits, deterministic row IDs, checksum
  conflict detection, and sanitized errors.

The claim and consumption table DDL currently belongs to the proof modules and
integration fixtures. It is not wired into migrations or application startup.
The consumption service still requires application admission, authorization,
source binding, and the production target submission adapter.

## Validation observed

Commands below ran from `backend/` in this worktree.

| Command | Observed result |
| --- | --- |
| `uv sync --locked` | Passed |
| `uv run pytest tests/unit --cov=app --cov-branch --cov-report=term-missing --cov-report=xml:coverage.xml` | 6,588 passed; 58% aggregate coverage. Ran before the storage test file and final journal edits; not final-tree full-suite evidence. |
| `uv run python -m tests.eval.report` | 48/48 scenarios, 165/165 checks |
| `uv run pytest tests/unit/test_stream_claims.py tests/unit/test_stream_consumption.py tests/unit/test_stream_contracts.py tests/unit/test_stream_storage.py tests/unit/test_stream_verification.py -q` | 63 passed |
| `NOVA_ORCH_SR_PORT=29030 NOVA_STREAM_GATE_ALLOW_RETENTION_CHANGE=1 uv run pytest tests/integration/test_stream_durability_gate.py -v -rs --junit-xml=stream-durability.xml` | 8 passed on the isolated StarRocks 4.1.4 engine |
| Changed-file `uv run ruff check --force-exclude ...` | Passed on Streams modules and their new tests |
| `git diff --check` | Passed with all new source/test/report files included as intent-to-add |
| `uv run python scripts/check_user_data_system_access.py` | Passed; no system/root shortcuts found in user-data adapters |

The eight engine checks cover label recovery, zero-row receipts, simultaneous
claimants, separate-process claimants, the actual claim repository, monotonic
offset receipts, strict INSERT failure, and claim preservation after real label
eviction. The offset persistence test uses explicitly synthetic receipts; the
target verification test independently uses real engine loading evidence.

The expiry test modifies label retention only when
`NOVA_STREAM_GATE_ALLOW_RETENTION_CHANGE=1`. It requires a disposable FE with
`label_clean_interval_second=1` and restores `label_keep_max_second` afterwards.
Never enable this test against a shared or production engine.

## Required integration gate has not passed

An isolated stack was created with Compose project `nova-streams-proof`, using
the repository test Compose file. `mysql:8.0` was pulled and the standard seed
script completed. Other Compose projects were left intact.

The full command was:

```sh
uv run pytest tests/integration -m engine -v -rs \
  --log-cli-level=WARNING --junit-xml=integration.xml
```

It used the CI test endpoints and throwaway signing/encryption values, with
`COMPOSE_PROJECT_NAME=nova-streams-proof` and the Docker Desktop proxy host.
The run was interrupted after 357.95 seconds while
`test_intelligence_studio_live.py::test_bootstrap_and_review_use_production_api_with_restricted_identity`
was not progressing. It ended with exit code 2, 33 passed, 8 skipped, and 30
deselected. This is not a successful integration run. The skipped governed tests
require an initialized isolated patched-FE/Ranger installation.

An isolated rerun of that single Studio test with `--full-trace` and
`-o faulthandler_timeout=60` also exceeded an explicit 180-second subprocess
deadline and returned wrapper exit code 124. The trace only established a wait
inside the asyncio selector; it did not establish the underlying cause. No
existing Studio implementation or assertion was changed to bypass this gate.

The task-owned `nova-streams-proof` stack was stopped after testing to release
resources. Its containers are retained for resumption; other projects were not
stopped. Diagnostic logs are in `/tmp/nova-streams-{unit,eval,integration,studio-diagnosis}.log`.

Required validation and the requested implementation remain incomplete. After
reviewing this status, the user explicitly requested committing, pushing, and
opening a PR on 2026-10-05. This authorizes publishing the foundation as a draft
PR despite the outstanding gates; it does not establish release readiness or
completion of Nova Streams V1. The 63 focused unit tests and changed-file Ruff
were rerun successfully before publication. Generated XML reports are excluded
from the commit.

## Remaining implementation

1. Complete the durability gates for in-flight engine work and real socket-loss
   recovery, then integrate source and consumption admission with durable pending
   generations. Validate all crash boundaries, not only the tested primitives.
2. Add control-plane migrations/runtime/bootstrap parity, source registration,
   managed append, exact StarRocks schema mapping, contiguous publication, source
   identity and epoch checks, and a real provider implementation.
3. Enforce and verify the exclusive write deployment, all Nova writer paths,
   reserved internal labels, and source/target authorization. Reject unsupported
   Ranger filtering/masking without treating preview metadata as authorization.
4. Implement typed SQL grammar/AST/actions, relation binding, source-column-only
   wildcard expansion, execution snapshots, system functions, and secret-safe
   lowering across HTTP, proxy, task, assistant validation, and other consumers.
5. Integrate task dependency extraction, durable dedup/watermarks, overlap and
   missed-event reconciliation, recovery, retention/GC with durable snapshot pins,
   and feature-disable recovery behavior.
6. Add API/Explorer surfaces, metrics/audit, release documentation and benchmarks.
7. Run final-tree full unit/eval/integration, governed Ranger acceptance, grammar
   regeneration/drift, frontend checks, and final diff review. Synchronize with
   remote main before commit and again before push.

The provider protocol or these proof modules must not be described as a real
production provider. No production provider is available in this worktree yet.
