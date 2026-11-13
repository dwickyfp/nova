# Stage PostgreSQL verification

This checkpoint prepares Stage metadata storage and its actor-bound command
service. It does not switch the running API, SQL, notebook or ML callers to PG.
The schema, role matrix and remaining ports are documented in
[PG Stage registrations](../../../architecture/production-refactor/PG_STAGE_REGISTRATIONS.md).

Testing reuses PostgreSQL 17.11 with verified TLS in the existing local `docker`
project. Fixtures refuse pre-existing Nova metadata schemas/roles and clean up
only their owned synthetic state. No development Stage row, object-store file
or StarRocks data was reset. Ranger responses in these PG tests are controlled
at the existing effective-access boundary; these are not new live Ranger tests.

The first focused run had 51 passes and two fixture failures:

- The backup SQL identity was passed through application UoW startup, which
  correctly refused it. The negative grant test now uses that identity directly
  with a complete actor context to verify both SQL surfaces are forbidden.
- The negative action test supplied an invalid managed prefix, so the namespace
  constraint rejected it before RLS. The fixture now supplies a valid managed
  identity/prefix and verifies that read scope cannot insert a registration.

The next focused run passed 75 cases without skips. It includes real SQL RLS,
column grants, runtime identity/prefix constraints, schema-based colleague
access, concurrent creation/deletion, audit rollback, cancellation, current
context checks, import preservation and transaction-pool isolation. After that
run, the concurrency cases gained explicit barriers before competing writes,
and an upgrade-from-revision-0011 case was added. Final combined verification is
recorded in `commands.json`; the earlier 75-case run does not cover those last
test edits.

The first combined run had 485 passes and one failure in the new upgrade
fixture: its historical capability row omitted the required `owner_name`.
The fixture now supplies an original owner and asserts that owner survives the
upgrade along with the manifest. The failed 486-case report is retained; the
full suite was rerun after this correction: 486 passed, zero skipped.

The initial Ruff attempts caught long lines and an unused test import. They
were corrected before final checks. No test was disabled or assertion weakened.
Reports retain each failed attempt; raw logs and connection material are not
exported. No live embedding or other provider request is part of this phase.

Final local checks passed on the source/schema hashes in [commands.json](commands.json):

| Check | Result |
| --- | --- |
| Full backend unit/branch coverage | 10,800 passed, zero skipped; 65% combined coverage |
| Real PG17 and PgBouncer suite | 486 passed, zero skipped; includes 76 Stage cases |
| Changed Python Ruff | 94 files passed |
| Full application mypy | Passed; three existing untyped-body notes |
| Agent eval | 54/54 scenarios, 189/189 checks |
| User-data system-access boundary | Passed |
| Migration history | All eleven prior checksums unchanged; revision 0012 added |
| PG cleanup | Zero fixture-owned schemas/roles remain |

The frontend source, tests and dependencies are unchanged from `329eab3d`;
its passing lint/build and 1,605-test browser coverage remain applicable. CI
for this new checkpoint is recorded separately after push; local checks are not
represented as a GitHub Actions result.

Runtime caller wiring, refusal-audit integration, historical namespace/timezone
provenance, durable file-effect receipts, migration rehearsal and cutover remain
open. Passing this domain's storage checks is not all-feature release acceptance.
