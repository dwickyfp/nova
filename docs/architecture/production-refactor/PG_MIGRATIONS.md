# PostgreSQL schema installation

The one-shot command `python -m app.core.metadata_migrations --app-version SHA`
installs the revisions shipped in `backend/metadata_migrations/`. `SHA` is the
40-character source commit. The command reads its TLS DSN from
`NOVA_METADATA_MIGRATOR_DSN`; never put that DSN in command arguments or reports.
The separate settings class does not load `.env`. Production deployment will
supply the value only to the migration process through its private Compose file.

This is a tested schema installer, not a completed Nova metadata migration.
Revision `0001` creates only `schema_migrations` and `deployment_state`. All
application domains still use StarRocks. No current API lifecycle invokes this
installer, and it cannot publish a metadata epoch or import legacy rows.

## Ownership and admission

The database must be PostgreSQL 17 `nova_control`, with an explicit TCP hostname
and `sslmode=verify-full`. Unix sockets are refused because their TLS setting
would not authenticate a server. Bootstrap must provision the named roles from
the [approved source specification](source/NOVA_PRODUCTION_ARCHITECTURE_REFACTOR_SPEC.md):

- `nova_meta_owner`: NOLOGIN schema/table owner, without superuser, bypass-RLS,
  database-creation, role-creation or replication attributes.
- `nova_meta_migrator`: restricted login able to SET ROLE to the owner. The
  owner requires CREATE on this database; application logins do not.
- `nova_meta_app` and `nova_meta_readonly`: restricted access roles. Revision
  `0001` grants only schema USAGE and SELECT on the two deployment tables.
- `nova_meta_backup`: separate backup role. Revision `0001` gives it no SQL
  table access; this installer does not implement the backup/PITR service.

Role provisioning and per-workload login credentials belong to the separate
[bootstrap command](PG_BOOTSTRAP.md), not the application pool. That command
creates and verifies restricted roles before schema installation; its tests use
only the explicitly disposable fixture. Production Compose wiring, complete
domain grants and RLS policies remain to be delivered.

Application startup rejects schema/table/database ownership, schema or database
CREATE access, elevated attributes, membership in elevated roles, and broad
data/server-file privileges. It also checks the packaged migration checksums,
schema version and expected epoch. Missing or incorrect state prevents readiness.
An installed schema with a NULL epoch cannot serve application transactions.

## Atomic installation and recovery

Revision files have contiguous numbers starting at `0001`, stable names and
SHA-256 checksums over their exact UTF-8 bytes. Editing or renaming an applied
revision, omitting an applied revision, or finding an unexplained existing schema
refuses installation. The installer does not adopt existing tables as a baseline.

One transaction acquires the deployment advisory lock, validates the entire
applied prefix, runs pending DDL, records its ledger and updates schema version.
Every revision executes inside a PostgreSQL DO block nested in that transaction.
Explicit or dynamic COMMIT inside a revision fails and rolls everything back.
The reserved dollar delimiter is rejected in revision content. Migrations that
require work outside a transaction need a separately reviewed protocol; they
cannot be slipped into these revision files.

The owner revokes PUBLIC schema/default-object privileges. Future domain tables
receive no blanket application or observability grant. New grants must name
their intended consumers and accompany their authorization/RLS tests.

Every application transaction, including reads, holds a shared deployment lock.
The exclusive migrator therefore cannot change schema beneath an admitted
reader. Pending revisions on an existing deployment require maintenance. A
successful upgrade preserves its existing epoch and maintenance state. Re-running
an unchanged installation does not rewrite ledger timestamps or app versions.

Connection loss returns `MetadataOutcomeUnknown`; the command does not retry.
An operator can inspect the ledger using a fresh connection and rerun the same
immutable schema package. The ledger and DDL commit together, so that rerun either
verifies the committed prefix or applies a rolled-back revision. This recovery
applies only to PostgreSQL DDL. It does not authorize replaying external effects.

Schema installation leaves a fresh deployment in maintenance with a NULL epoch.
The later cutover procedure must validate the complete import, freeze all legacy
writers and publish its epoch. It must not infer authority from an installed
schema or a successful process exit.

## Verified boundaries

The current local PG17 suite passed 31 tests without skips on the existing TLS
fixture. It covers fresh installation, unchanged reapplication, checksum drift,
concurrent migrators, fresh/upgrade rollback, attempted COMMIT escape, connection
termination during DDL, bounded lock waiting, retained epochs, private grants,
startup refusal, revision CAS, actor isolation and typed-value round trips.
The subprocess test uses the ordinary one-shot CLI and verifies that its output
contains no connection password. Fixture cleanup refuses pre-existing resources
or changed ownership markers; no StarRocks or application data is reset.

These tests do not prove complete domain translation, production role bootstrap,
PgBouncer transaction pooling, PITR, backup restore, migration rehearsals, or
cutover. Those gates remain open in [TRACEABILITY.md](TRACEABILITY.md).

The transaction and role-membership implementation follows the
[psycopg transaction contract](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
and [PostgreSQL 17 role grants](https://www.postgresql.org/docs/17/sql-grant.html),
with the behaviors above exercised against the pinned PG17 fixture.
