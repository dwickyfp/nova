# Assistant, access-control, Superset, migration and AI usage metadata checkpoint

Source parent: `0e257fb0`. This checkpoint adds revision 0024 and five
repositories for the 14 remaining small tables (see
[the contract](../../../architecture/production-refactor/PG_ASSISTANT_POLICY_BI.md)).
Runtime callers are not cut over; nothing in the running application imports
the repositories.

Revisions 0019–0023 (Catalog, Ontology, Agents, Intelligence, Query autopilot)
were prepared in parallel. Migrations must be contiguous, so every check below
ran with temporary `0019_placeholder.sql` … `0023_placeholder.sql` files
(`PERFORM 1;`) that were deleted before commit. This branch merges only after
0019–0023 are in place; the real-PG suite must be rerun on the merged revisions.

## What the tests establish

All 738 real-PG tests in CI's list pass with zero skips on the existing PG17
TLS fixture (no other stack started), including 36 cases in
`test_metadata_assistant_policy_bi.py`:

- Conversations: another person cannot read, append to, rename, delete, give
  feedback on or reuse the id of someone's thread; a forged owner is refused.
  Administrators read across people for monitoring but change nothing.
- Four concurrent identical appends of one message id store one turn; a
  different turn under that id conflicts. Five concurrent appends get sequence
  numbers 3–7. Turn content cannot be changed; feedback only on answers.
- A turn stamped with another principal or role is refused.
- The learning service principal (task workload) sees only learning-queued
  questions, records and replaces trace steps, completes a question once; a
  person in that workload sees nothing; learning once disabled stays off.
- UI state: two concurrent saves from one revision, one winner; another role
  neither reads nor overwrites it; state must reference the owner's answer.
- Translations are shared across workloads and never move back a version.
- Role projections, policy state and data scopes: concurrent change from one
  revision has one winner; non-admin roles are refused or change no row;
  `ACCOUNTADMIN` projections are refused; audit rows carry the acting principal
  and role and never scope values; data-scope ids must be the source digest.
- Business policy: three concurrent saves from revision 1, one winner at
  revision 2; a stale save conflicts; only `ACCOUNTADMIN` saves; revisions are
  immutable.
- BI author roles: concurrent replaces from one set revision, one winner; a role
  row change without advancing the set revision is refused; audited.
- Publications: created once by id across three concurrent starts; advance only
  from the status read; terminal rows frozen; continued only in the starting
  role; another person sees no journal row but gets the successful business
  context for a chart through the fixed projection; never deleted.
- Semantic dataset map names the last publisher; only web writes.
- Migration sources: five plaintext-looking or malformed references refused with
  no audit row; scheme and ARN references kept; runtime names unique; audit has
  no reference. Jobs: created once; credential-named request members refused;
  owner-only reads; three concurrent claims, one wins; stale generation refused;
  terminal frozen; the owner deletes a finished job.
- Usage: append-only (update and delete refused), distinct identities,
  administrators read, others see nothing.
- Every one of the 12 workload logins gets exactly its reviewed table access.
- An unbound actor sees nothing; maintenance blocks writes but not reads; import
  is refused outside maintenance and keeps legacy rows (duplicate sequence
  numbers, duplicate source names, bare references, equal business-policy
  revisions, duplicate usage ids) without audit; equal policy revisions are
  refused on read.

Mutation check: making `apb_secret_reference` accept every value made all five
secret-refusal cases fail; the function was restored (byte-identical) before
the full run.

## Other checks

Unit/coverage 10,946 passed (0 skipped); 358 evaluation trajectories; scorecard
54/54 scenarios, 189/189 checks; `mypy app` clean; Ruff clean on the changed
Python files (rerun on the final source). The unit, trajectory and scorecard
runs started before the last two edits (task learning read scope in 0024, and
completion without `RETURNING` in the assistant repository); neither file is
imported by those suites. Delivered-commit CI has not been checked.

Retained failed attempts on the module (before the full run):

1. 32 passed, 4 failed: three test queries used an unescaped `%` with bound
   parameters; the learning completion was refused (42501).
2. 35 passed, 1 failed: dropping `RETURNING` was not enough. PostgreSQL also
   checks an updated row against the SELECT policy when the `UPDATE` reads
   rows, so a completed question must stay in the learning read scope. The
   policy now admits pending and completed learning questions only.
3. 36 passed.

## Remaining

Wiring into routers, the assistant loop, learning handlers, access-control
service, Superset SSO/publication/sync, migration worker and the AI function
gateway; service principals for the usage flush and ontology learning; import
of all 14 tables with timestamp provenance; usage partitioning and retention;
rehearsals and the shared cutover. Findings 1–11 are in the contract.
