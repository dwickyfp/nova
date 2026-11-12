# Intelligence metadata storage checkpoint

## Revision 0025 (remaining 11 tables)

Source parent: `852a14e1`. Revision 0025 prepares `CONFIG_ENTITIES`, the six
feature-store tables, the three legacy agent semantic-model tables and
`AUDIT_SEMANTIC_QUERY_USAGE`, completing all 36 Intelligence tables in storage.
Callers are not cut over.

Local chain: real `0017`/`0018` from `feat/production-control-plane`
(`0e257fb0`), this branch's `0022`, and temporary `PERFORM 1;` placeholders for
`0019`–`0021`, `0023` and `0024`; none of those are part of the commit. Function
bodies were first compiled in a rolled-back transaction on a scratch PG container.
Every PG run held `/tmp/nova-metadata-pg.lock` with no other metadata PG pytest
running.

- Real PG: `test_metadata_intelligence.py` 50 passed, 0 skipped on the first
  run (19 new cases: entity registration once, revision-CAS retirement with one
  winner, immutable keys; Feature View versions allocated once under
  concurrency, failed versions never served, finished once; group members must
  pin built versions of the group entity's views; a pinned view cannot be
  dropped; a concurrent drop and pin never both succeed; training sets owned and
  model links only to the caller's own set; legacy semantic models read-only
  with concurrent counter increments (3+4 uses), forward-only counters and
  owner-scoped usage; per-workload privileges for every role in
  `WORKLOAD_ROLES`; maintenance-only import of an interrupted legacy build).
  Full CI metadata list (20 modules): 692 passed, 0 skipped.
- Mutation check: removing the pin check from the Feature View delete guard made
  the pinned-view test fail ("DID NOT RAISE"); restored. The concurrent
  drop-versus-pin case still passed under that mutation because the drop won the
  race in that run, so it is a race check, not the mutation's detector.
- Unit/coverage 10,946 passed (0 skipped); trajectories 358; scorecard 54/54
  scenarios, 189/189 checks; `mypy app` clean; Ruff clean on changed Python files.

## Revision 0022

Source parent: `c6a45bac`. Revision 0022 and `IntelligenceMetadataRepository`
prepare 25 of the 36 Intelligence tables in PostgreSQL (Semantic Views and
versions, 17 revision stores, News feedback, five AI Search tables). Runtime
API, agent, task and maintenance code is not cut over, and 11 tables remain
(feature store, entities, agent semantic models, semantic query usage). See
[the contract](../../../architecture/production-refactor/PG_INTELLIGENCE.md).

## Setup

Local device, the existing shared PG17 TLS fixture and pinned PgBouncer build;
no other stack started. Revision 0022 was developed in its own worktree while
other sessions prepared 0017–0021, so local runs used the real `0017` (Streams)
and `0018` (ML engine) files from `feat/production-control-plane` (`0555b1ad`)
and temporary `0019`–`0021` placeholders (`PERFORM 1;`). Neither the placeholders
nor the copied 0017/0018 files are part of this commit. Every PG run reported
below held `/tmp/nova-metadata-pg.lock`, and no other metadata PG pytest process
was running when it started.

## Results

- Real PG, final run: `test_metadata_intelligence.py` 31 passed, 0 skipped; the
  full CI metadata list (20 modules, including this one) 673 passed, 0 skipped.
  Cases: one winner for concurrent Semantic View versions from one latest
  version, stale and gapped version numbers refused; publication only from a
  passing validated version, concurrent publishes with one winner, stale
  null-safe active pointer refused; published versions immutable (validation,
  status, definition, fingerprint, activation time) and only retired; pointer to
  an unpublished version refused; raw change without the next revision refused;
  versions deleted only after their view; News settings revisioned and
  attributed; concurrent record revisions with one winner, stale revision
  refused, idempotent re-save; owner isolation by principal and role, guessed-id
  takeover refused, shared decision/action-review lookups, reviewer append with
  `written_by`; payload/row mismatches refused; append-only history; Business
  Event writer binding; News editions read by execution scope; reader-private
  feedback; Search versions created once under concurrency, stale build claims
  fenced for progress and results, raw stale sync and invalid transitions
  refused, activation CAS; query log and evaluations bound to the caller;
  privileges for every role in `WORKLOAD_ROLES`; maintenance refusal; import
  refused outside maintenance and keeping a legacy published view, a legacy
  version and a same-revision branch that then blocks further writes.
- Mutation check: removing the scope-continuity check from the revision guard
  made the takeover test fail ("DID NOT RAISE"); restored, verified by diff.
- Unit/coverage: 10,946 passed, 0 skipped. Trajectories: 358 passed.
  Scorecard: 54/54 scenarios, 189/189 checks. `mypy app` clean. Ruff clean on
  the changed Python files.

## Failed attempts, kept

- The first module run failed during migration with an opaque
  `MetadataOperationFailed`. Cause: the requested `SELECT 1;` placeholders are
  invalid inside the migrator's PL/pgSQL wrapper (SQLSTATE 42601). Placeholders
  were changed to `PERFORM 1;`. A throwaway probe test used to find this was run
  and then deleted; one probe attempt overlapped other sessions' fixture runs
  and was stopped by the coordinating session, so it is not evidence.
- The first passing migration run had 29 passed, 2 failed: two tests caught an
  expected refusal inside an open transaction, where the driver error is only
  translated when the transaction exits. The tests were corrected to wrap the
  whole transaction.
- A first draft of the migration created UPDATE policies with a wrong format
  argument; it was corrected before any run.
- The worktree started without a synced environment, so the first `mypy` output
  was environment noise; after `uv sync --locked --offline` (and scipy 1.17.1 for
  this Darwin host, lockfile untouched) `mypy app` is clean.

Delivered-commit CI is not yet checked; the coordinating session reruns the
combined suite after merging revisions in order.
