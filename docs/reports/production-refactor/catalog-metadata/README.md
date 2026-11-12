# Catalog metadata storage checkpoint

Source parent: `c6a45bac`. Revision 0019 and `CatalogMetadataRepository`
prepare the thirteen Catalog tables in PostgreSQL. Running API, SQL, refresher,
agent and write-back code is not cut over. See
[the contract](../../../architecture/production-refactor/PG_CATALOG.md).

## Results (local device, existing PG17 TLS fixture, no other stack)

Every real-PG run held the shared fixture lock; the three single-hold runs
(module, mutation, CI list) also recorded that no other `test_metadata_` pytest
process was running. Revisions 0017 (Streams) and 0018
(ML engine) were the real files from the integration branch, present only for
the run and not part of this commit; early runs used `PERFORM 1;` placeholders.

- Real PG, CI list (20 modules): 671 passed, 0 skipped (lock held 19:43–19:56),
  including 29 Catalog cases:
  - one success and one conflict for concurrent catalog, connection and
    annotation edits from one revision; raw definition change without a new
    revision refused;
  - an older sync generation refused for object upserts, tombstones, retirement
    and status; a claim blocked until an in-flight harvest committed;
  - derived ids for objects, columns, securables, lineage and links; forged ids
    and parents refused; drop/return timestamps from the database clock;
  - sync runs created once under four concurrent starts, finished once, frozen;
  - credentials stored only as `enc:` Fernet ciphertext; plaintext, fake tokens
    and credentials of missing connections refused without echoing the value;
    write-back ciphertext unreadable by web/query/proxy and readable by
    notebook/task; read ciphertext unreadable by notebook/task and agent; audit
    rows free of usernames, passwords and ciphertext; connection deletion removes
    both credential rows;
  - credential-named catalog properties and connection options refused;
    `secret_ref` must be a reference;
  - stewardship: owners and catalog/database annotations or tags need a
    security-admin role; classification only on columns; tag creators kept;
  - lineage spans only widen for out-of-order harvests; naive times refused;
  - owner links replaced as one set; per-workload grants for every role in
    `WORKLOAD_ROLES`; maintenance refuses writes but not reads; migrator import
    only under maintenance, keeping legacy ids, revisions and an orphan object.
- Mutation check: with the sync-generation comparison disabled, the two fencing
  tests failed (`DID NOT RAISE MetadataConflict`); the migration was restored
  and the passing run above used the restored file.
- Failed attempts, fixed before the passing run:
  1. `SELECT 1;` placeholders cannot run inside the migrator's PL/pgSQL `DO`
     wrapper (no result destination); replaced by `PERFORM 1;`.
  2. Migration 0019 did not compile (42601): a `CASE … THEN` inside an `IF`
     condition ended the condition early; the `CASE` is now parenthesized.
  3. Two credential tests failed (42501): the credential upsert read
     `EXCLUDED` ciphertext, which web must not select. Credential entry is now an
     update followed by an insert.
  4. One connection test failed (2201B): a `{1,1000}` regular-expression bound
     exceeds PostgreSQL's limit of 255; the reference pattern now uses `+` and
     the column length bounds it.
- Unit/coverage 10,946 passed (0 skipped); `tests/eval` 358 passed; scorecard
  54/54 scenarios, 189/189 checks; `mypy app` clean; Ruff clean on the changed
  Python files. The unit suite ran before fixes 3 and 4, which change only the
  new repository and migration; neither is imported by the unit suite.

Committed locally on the worktree branch, not pushed; the integration branch
owner merges revisions in number order. Delivered-commit CI is not yet checked.
