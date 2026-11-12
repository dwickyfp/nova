# Ontology metadata storage checkpoint

Source parent: `c6a45bac`. Revision 0020 and `OntologyMetadataRepository`
prepare the twelve business ontology revision stores and their head table in
PostgreSQL. Ontology services, Studio tools and the evaluation runner are not
cut over. See [the contract](../../../architecture/production-refactor/PG_ONTOLOGY.md).

## Results (local device, existing PG17 TLS fixture, no other stack)

Revisions 0017 (Streams) and 0018 (ML engine) were the real files from
`feat/production-control-plane` (`0555b1ad`); 0019 was a temporary
`PERFORM 1;` placeholder, removed before commit. Every PG run below held
`/tmp/nova-metadata-pg.lock` in the same shell and recorded no other
`test_metadata_*` pytest process before or after.

- Real PG, CI's 20-module list: 665 passed, 0 skipped, including 23 ontology
  cases: two concurrent revisions of one record and two concurrent first
  revisions each end in one success and one conflict; the source save protocol
  (unchanged content is a no-op, stale or invented expected revisions refused);
  workloads cannot update, delete or truncate revisions or touch heads, and a
  raw insert must be the next revision with a matching payload; imported
  same-revision forks are kept, reported by `branches`, refused by `current`,
  `latest` and `revision`, block runtime writes, and are resolved by a
  maintenance-only appended revision; a run's evaluation results are read
  through the `run_id` index (plan checked) and a batch with a stale record
  writes nothing; stewardship refusals in storage (non-admin domain, submitter
  not the caller, proposal not starting pending, self-review, non-steward
  review, review in another's name, status not implied by reviews, content
  change, non-steward apply, non-submitter withdraw, applied and withdrawn
  final, concept writes only under an approved proposal) with the success
  audit sequence; release preparation and publication only by ACCOUNTADMIN,
  published only after the alias points at it and never changed after, alias
  moves checked against the previous and base release; per-workload grants for
  every role in `WORKLOAD_ROLES`; actor mismatch refused; maintenance refusal;
  and maintenance-only import that keeps actor and time and refuses deletes.
- Mutation check: removing the runtime `(id, revision)` unique index made the
  concurrent-revision test fail (two rows committed); restored, then the full
  list above passed.
- Defects found and fixed before the passing runs: the first placeholders were
  `SELECT 1;`, which fails inside the migrator's `DO` block; and an expression
  index on `payload->>'run_id'` was never used under forced RLS because `->>`
  is not leakproof, so the migration now stores a generated `run_id` column and
  indexes it.
- An earlier run of this module was stopped by the coordinating session because
  another PG run overlapped it; its result is not used.
- Unit/coverage 10,946 passed (0 skipped); trajectories 358 passed; scorecard
  54/54 scenarios, 189/189 checks; `mypy app` clean (703 files); Ruff clean on
  changed Python files.

Committed locally on the session branch for the main session to merge in
migration order; not pushed. Delivered-commit CI is not yet checked.
