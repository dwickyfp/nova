# Business ontology metadata port

Revision 0020 prepares the twelve ontology revision stores in `nova_meta`
(`CONFIG_ONTOLOGY_DOMAINS`, `_CONCEPTS`, `_RELATIONS`, `_REPRESENTATIONS`,
`_PROPOSALS`, `_RELEASES`, `_ALIASES`, `_DERIVED`, `_COMPILATIONS`,
`_EVAL_CASES`, `_EVAL_RUNS`, `_EVAL_RESULTS`) plus one PG-only table,
`config_ontology_heads`. `OntologyMetadataRepository` is the prepared
replacement for the ontology use of `RevisionRepository`, with the same method
shapes (`latest`, `revision`, `latest_many`, `history`, `current`,
`current_matching`, `save`, `save_many`). Running code still uses
`NOVA_SYSTEM`; nothing imports the repository. The three Business Event tables
that share `RevisionRepository` belong to the intelligence domain and are not
part of this revision.

## Revision model

The source model is kept: each change is a new `(id, revision)` row, rows are
never changed, and the newest revision of an id is current. Releases pin exact
member revisions, which stay readable because no workload can update, delete
or truncate a revision row.

- **One row per runtime revision.** A runtime row must be exactly the next
  revision of its id (`head + 1`), and a partial unique index on
  `(id, revision) WHERE provenance='runtime'` lets only one of two concurrent
  writers commit; the other gets 409. This replaces the Redis metadata lease.
- **Imported forks are kept and reported.** The import keeps every source row,
  including two rows with the same `(id, revision)` (possible in the source
  after a lost lease). `config_ontology_heads.branches` counts them; `current`,
  `latest`, `latest_many`, `revision` and `current_matching` raise 409
  "Concurrent revisions require reconciliation" as the source does, and
  `branches(kind)` lists the forked ids. A runtime write cannot build on a fork.
  Reconciliation appends a resolving revision through the migrator under
  maintenance.
- **Current reads use heads.** An `AFTER INSERT` trigger keeps one head row per
  record. "Current" joins heads to the revision table by key instead of
  windowing over every row of a kind.
- **A run's evaluation results** are read through an index on a stored
  `run_id` column generated from the payload, joined to heads, not by
  windowing over all results. (An expression index on `payload->>'run_id'`
  is not used under forced RLS, because `->>` is not leakproof.)
- **Batches are one transaction.** `save_many` runs in the caller's transaction;
  a conflict leaves nothing written, closing the Wave H gap where a batch was a
  series of independently committed chunks.
- Row columns `actor`, `active_role`, `security_context_version` and
  `created_at` are set from the bound actor and the database clock for runtime
  rows; the record payload is stored as written.

## Access

Ontology records are deployment-wide; the actor is provenance, not an
ownership predicate. Access is per workload through a bound actor context, and
forced RLS applies to every table:

| Workload | Read | Insert |
| --- | --- | --- |
| web (ontology pages, stewardship, releases, evaluation, Semantic View publish) | all kinds and heads | all kinds |
| agent, automation (Studio tools, scheduled agent runs) | all kinds and heads | proposals, representations, derived, compilations |
| any other workload | none | none |

Domain visibility (`access.can_see`) and the read-time filters stay in the
service. The row guard repeats the governance rules that can be decided from
stored rows:

- domains, releases and the current alias require `ACCOUNTADMIN` as the active
  role; a domain's parent must exist;
- a proposal starts `PENDING` with the caller as submitter, keeps its content,
  and only appends reviews. A review is by the caller, never the submitter, for
  domains of the proposal that the active role stewards. The status must be the
  one its reviews imply. Only the submitter withdraws; only a steward of one of
  its domains or `ACCOUNTADMIN` marks it applied; applied, rejected and
  withdrawn are final;
- concepts, relations and representations are written only while their
  proposal is `APPROVED`, by a steward of one of its domains or `ACCOUNTADMIN`.
  A Semantic View publication may re-pin or stale an existing representation
  under the proposal that created it, keeping its concept;
- a release is prepared, then published once, unchanged, and only after the
  current alias already points at it. An alias move names the release it
  replaces, and moving to a prepared release requires that release to be based
  on the current one.

Governed changes (domains, concepts, relations, representations, proposals,
releases, alias) write a success audit row in the same transaction. Derived
items, compilations and evaluation rows are compiler and scoring output and are
not audited, as in the source.

## Import

The migrator imports as the owner only while `maintenance` is on, under the
exclusive deployment lock. Imported rows keep their actor, role, context
version, timestamp and payload exactly, skip runtime lifecycle rules and are
marked `provenance='imported'`; heads are built by the same trigger. Even the
importer cannot update, delete or truncate revisions.

## Findings not fixed in storage

- Legacy `created_at` is a StarRocks `DATETIME` without zone written with engine
  `NOW()`; the import must state the engine time zone explicitly.
- The source payload is JSON text; `jsonb` drops duplicate keys and key order.
  The import must compare decoded payloads and record any duplicate-key rows.
- The source wrote revision rows and the governed audit in separate statements,
  and a proposal's apply wrote several kinds outside one transaction. In PG the
  caller must run apply, its audit and the proposal's `APPLIED` revision in one
  metadata transaction; the storage cannot force callers to do so.
- Evaluation run steps copy the run record, so a payload `actor` can name the
  run's starter while the row columns name the writer.
- Ontology refusals the service audits as `DENIED` are not written by the
  storage; refused inserts roll back. The service keeps the denial audit.
- Imported concepts or relations whose proposal is missing or not `APPROVED`
  stay readable, but any later runtime change to them needs a new approved
  proposal. A representation imported without a proposal row can still be
  re-pinned (it keeps its proposal id).
- Domain visibility, `can_see` path checks and model-exposure classification
  are not row policies: the service applies them to deployment-wide reads.

## Remaining work

Wire the repository into `stewardship`, `releases`, `service`, `derived`,
`representations`, the evaluation service and the Studio tools at cutover,
each in one metadata transaction with its audit; write the explicit import
transform; reconcile any forks the import reports.

Validation is recorded in
[the ontology metadata checkpoint](../../reports/production-refactor/ontology-metadata/README.md).
