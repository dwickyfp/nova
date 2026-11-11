# WP-02: PostgreSQL foundation

Status: **IN_PROGRESS**. The transaction boundary and one-shot schema installer
are implemented. Complete domain schemas, deployment provisioning, PgBouncer
acceptance and production readiness remain open. No metadata cutover occurred.

Source checkpoint: parent `e1cb5f928a8f727a3fab8dcdc0f1ef698287f718`, application
tree `e3bcb3f5604f20c68c98e8beb3f5e9c67abfcee416c7ab4767dc8954ae546759`.
Schema revision: `0001_deployment_state.sql`, two internal deployment tables,
with a NULL epoch after fresh installation. Fixture ledger app versions are
synthetic test values, not release versions.

`core/metadata_db.py` owns actor-bound pooled transactions, admission and
redacted failures. `core/metadata_schema.py` validates ordered immutable files.
`core/metadata_migrations.py` owns the separate one-shot migration connection.
`core/config.py` keeps its private DSN out of repr/errors and does not read `.env`
for this command. [Operator details](../../../architecture/production-refactor/PG_MIGRATIONS.md)
describe ownership, grants and recovery.

No public API, SQL syntax, object identity or user data format changed in this
checkpoint. PG application startup now requires the packaged migration ledger,
rejects broad inherited privileges and requires an explicit TLS hostname.
Reads join writes in holding the shared deployment lock. Application readiness
still requires a matching published epoch, which the installer cannot create.

## Validation and topology

Local tests reused `docker-nova-metadata-db-1` in the existing `docker` project:
PostgreSQL `17.11 (Debian 17.11-1.pgdg12+2)`, pinned image and verified TLS.
The fixture creates disposable roles/schema only after proving their absence,
then verifies its ownership marker before cleanup. Final inspection found zero
test schemas/roles after excluding the pre-existing fixture administrator.
StarRocks, Ranger, object storage and shared Redis were not mutated by this suite.

The executed command was `uv run python -`, with a wrapper that loads the private
fixture connection into `NOVA_TEST_METADATA_ADMIN_DSN`, sets the explicit
disposable acknowledgement, then invokes:

```python
pytest.main([
    "tests/integration/test_metadata_migrator.py",
    "tests/integration/test_metadata_pg_foundation.py",
    "-q", "--tb=short", "--junit-xml=/tmp/nova-migrator-pg-4.xml",
])
```

Result: **31 passed, zero skipped, 18.80 seconds**. The accompanying
`metadata-pg-local.xml` removes the local hostname and retains all testcase
results. The CI PostgreSQL job now runs both files and refuses empty/skipped
acceptance reports; CI on this new source is still pending.

| Requirement | Concrete evidence | Remaining limit |
| --- | --- | --- |
| DB-01/02 | Fresh installer/CLI, no duplicate revision, unchanged timestamps on reapply | Only foundation tables exist; domain installation is not complete |
| DB-03/04 | Changed applied bytes and wrong startup ledger refused; two migrators produce one revision | Deployment role bootstrap is not installed by this command |
| DB-05 | Partial fresh/upgrade DDL, explicit/dynamic COMMIT and terminated connection roll back; re-run succeeds | No full legacy import rehearsal yet |
| DB-06/09 | One winner under revision CAS; bounded statement/lock behavior and rollback | Pool exhaustion, full load and every deadlock/failure case remain open |
| DB-10/12 | Transaction-local actor isolation, no raw-connection scoped access, denied DDL and broad role grants | No PgBouncer test or complete domain RLS coverage |
| DB-11 | Exact decimal, aware UTC timestamp, Unicode/JSON array, boolean and SQL/JSON NULL round trips | Legacy per-column transforms/timezone provenance remain open |

Full-application mypy and changed-file Ruff (47 Python files) passed. The agent
scorecard passed 54/54 scenarios and 189/189 checks. Full backend coverage passed
10,495 tests with four warnings in 521.74 seconds, with 65% combined coverage.
An earlier unit run was deliberately interrupted after adding
the inherited-privilege gate; it is not reported as a passing full suite.
Frontend code is unchanged since the separately verified MCP checkpoint.

## Security and recovery

This addresses parts of the specification's PG privilege, schema-epoch and
unsafe-restore boundaries, not the complete SEC-03/SEC-23 acceptance. App and
observability roles receive only explicit deployment-table reads in revision
0001; future domain grants must be reviewed individually. No migration function
receives an authenticated user's analytical-data credential.

On failure, keep writers frozen and inspect the authoritative PG ledger using
a fresh connection. Re-run the same immutable DDL package only after verifying
its checksum prefix. DDL and ledger are one transaction, so no external effect
is replayed by that recovery. Do not publish an epoch to bypass missing import,
rehearsal or writer-freeze gates. Existing StarRocks domains remain unchanged
until the separately validated one-way cutover.
