# ML engine metadata storage checkpoint

Source parent: `ed4afed3`. This checkpoint adds revision 0018 and
`MLMetadataRepository` for `CONFIG_ML_JOBS`, `ML_MODELS`, `ML_MODEL_VERSIONS`,
`ML_MODEL_ALIASES`, `ML_EPHEMERAL_RUNS` and `ML_RUNS` (the three `_PK` helper
tables resolve into the canonical tables at import). Runtime callers are not
cut over; nothing in the running application imports the repository. See
[the contract](../../../architecture/production-refactor/PG_ML_ENGINE.md).

Revision 0017 (Streams) was prepared in parallel. Migrations must be
contiguous, so every check below ran with a temporary `0017_placeholder.sql`
(`PERFORM 1;`) that was deleted before commit. This branch merges only after
0017 is in place; the real-PG suite must be rerun on the merged revisions.

## What the tests establish

All 671 real-PG tests in CI's list pass with zero skips on the existing PG17
TLS fixture (no other stack started), including 29 ML cases:

- Jobs: four concurrent creates of one id produce one job for the bound person,
  role and security version; another person's repeat conflicts; a job cannot be
  queued in someone else's name; the web login cannot read the encrypted SQL.
- Three concurrent claims: one wins with lease generation 1. A worker with a
  wrong generation cannot heartbeat or finish; cancellation is seen through the
  heartbeat; a finished job is frozen. The waiting API process can only cancel
  or withdraw an unclaimed job. Queue and cancel are audited; claims are not.
- An interrupted worker cannot finish; retention prunes a day-old failed job
  but keeps the interrupted one.
- Four concurrent reservations of one scope produce one model and versions
  1–4; another person's model of the same name is separate.
- Two concurrent publishes: one wins; a wrong reservation token conflicts;
  published versions refuse any change; publishing needs a URI, a lowercase
  SHA-256 and a non-negative size. The sweep can abandon but not publish.
- Model changes need the next revision, keep identity, and move the current
  version only forward to a published version.
- Aliases name only a published version of their owner's model and move from
  the revision read (two concurrent moves: one wins).
- Only the owner deletes a model; versions must go before the model.
- Ephemeral descriptors keep their scope; the sweep deletes only expired rows.
- Run records are created once, finish once and are visible only to their owner.
- Every one of the 12 workload logins gets exactly its reviewed access.
- Maintenance blocks writes but not reads; import works only under maintenance
  and keeps legacy rows, duplicate scopes, `__legacy__` aliases and inline
  payloads; an imported running job cannot be finished without a claim.

A mutation check that removed "a published version is immutable" from the row
guard made the immutability test fail; the guard was restored.

## Other checks

Unit/coverage 10,946 passed (0 skipped); 358 evaluation trajectories; scorecard
54/54 scenarios, 189/189 checks; `mypy app` clean; Ruff clean on the changed
Python files. Delivered-commit CI has not been checked.

Retained failed attempt: the first full real-PG run, made while the unit suite
ran on the same machine, had 667 passed with one failure and three errors, all
deadline or availability errors in `test_metadata_pg_foundation` (statement
timeout) and `test_metadata_pgbouncer` setup. The rerun without concurrent load
passed 671/671.

## Remaining

Wiring into the job submitter, worker, registry, ephemeral cache, sweep,
Decision Lab and router; interrupted-job reconciliation; model-deletion intent;
run retention; import of `_PK`/`_LEGACY` copies, legacy timestamps and inline
payloads; rehearsals and the shared cutover. Findings F1–F8 are in the contract.
