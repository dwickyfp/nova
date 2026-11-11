# PostgreSQL identity provisioning

`python -m app.core.metadata_bootstrap` provisions identities before the schema
installer. It requires a dedicated PostgreSQL 17 `nova_control` database, verified
TLS and its password-authenticated superuser database owner. Use the direct PG
endpoint. The command does not create a database, install domain tables, publish
an epoch, rotate existing passwords or connect to StarRocks.

This command is implemented and tested on the existing disposable PG fixture.
The production Compose and its private credential configuration are still to be
delivered; current Nova domains continue to use StarRocks.

## Private configuration

The separate settings class reads these environment values without `.env`:

| Variable | Value |
| --- | --- |
| `NOVA_METADATA_BOOTSTRAP_DSN` | Direct administrative DSN with `dbname=nova_control`, explicit TCP hostname and `sslmode=verify-full` |
| `NOVA_METADATA_BOOTSTRAP_DEPLOYMENT_ID` | Stable, nonzero deployment UUID; keep the same value across restarts |
| `NOVA_METADATA_BOOTSTRAP_ROLE_PASSWORDS` | JSON object from every login name below to its distinct password |
| `NOVA_METADATA_BOOTSTRAP_STATEMENT_TIMEOUT_MS` | Optional statement budget, 60,000 ms by default |
| `NOVA_METADATA_BOOTSTRAP_LOCK_TIMEOUT_MS` | Optional deployment-lock budget, 5,000 ms by default |

Every password must have 24–256 characters, contain no NUL and be distinct from
the other login passwords and an explicitly supplied administrative password.
Generate random values; the length check does not establish entropy. Missing,
extra, repeated JSON keys and common placeholder values are refused. The JSON
map and DSN are secret fields, and neither belongs in CLI arguments or reports.
The deployment's actual Compose remains outside Git with restricted file access.

## Roles and grants

- `nova_meta_owner` and `nova_meta_app` cannot log in. Only the owner receives
  database CREATE. The app group receives deployment-table reads from schema
  revision `0001`, not blanket access to future domain tables.
- `nova_meta_migrator` can explicitly SET ROLE to the owner, without inheriting
  it automatically or granting that membership onward.
- `nova_meta_readonly` receives the explicit reads in schema migrations.
- `nova_meta_backup` alone has REPLICATION. It receives no SQL table reads from
  revision `0001`. Physical backup/PITR and replication HBA acceptance remain open.
- Workload logins are `nova_meta_web`, `nova_meta_query`, `nova_meta_proxy`,
  `nova_meta_agent`, `nova_meta_scheduler`, `nova_meta_task`,
  `nova_meta_ingestion`, `nova_meta_ml`, `nova_meta_notebook`,
  `nova_meta_automation`, `nova_meta_maintenance` and `nova_meta_outbox`.
  They inherit the app group's common reads but cannot SET ROLE to it or to the
  owner. Future domain migrations must grant each workload's reviewed access.

All logins receive CONNECT explicitly. PUBLIC database CONNECT/CREATE/TEMP and
public-schema privileges are revoked. No managed role has superuser, CREATEDB,
CREATEROLE or BYPASSRLS. There are 15 logins and two NOLOGIN roles; these are
infrastructure identities, not a replacement store for Nova user authentication.

## Atomic setup and reapplication

One transaction takes the exclusive metadata deployment lock and creates the
roles, memberships, database grants and deployment markers together. A database
with unexplained roles, schemas, objects, grants or a deployment marker is not
adopted. A partial role set is refused rather than filled in around unknown state.

Passwords are converted to SCRAM verifiers through
[libpq's password API exposed by psycopg](https://www.psycopg.org/psycopg3/docs/api/pq.html#psycopg.pq.PGconn.encrypt_password).
Plaintext is not placed in DDL. Statement, error-statement and duration/sample
statement logging are suppressed inside this privileged setup transaction so
verifiers are not logged either. This is scoped to provisioning, not a global
change to runtime audit logging.

Reapplication verifies the deployment markers, exact role attributes,
memberships, database/public-schema grants, SCRAM verifiers and absence of
uncontrolled role settings. It performs no password replacement or silent grant
repair. After commit, a real authenticated connection verifies every configured
login. A failed login reports `BootstrapVerificationFailed`; already committed
identities remain available for inspection. A lost provisioning commit reports
`MetadataOutcomeUnknown` without an automatic retry. Re-running the same private
configuration verifies the marker and roles or installs a rolled-back setup.

The success result is `IDENTITIES_VERIFIED`, with the login count and whether
roles were created. It always reports `metadata_epoch_published=false`. Run the
[schema installer](PG_MIGRATIONS.md) afterward. Neither command establishes a
completed import or authorizes reopening writes after a migration.

## Verification and remaining boundaries

The current combined local PG17 suite passes 59 cases without skips, including
28 provisioning cases. Every workload login can read deployment state after the
installer and is denied DDL, temporary tables, authority updates and owner/app
role switching. Other cases cover stable reapplication, concurrent provisioning,
wrong credentials, foreign/partial state, privilege and role-setting drift,
ordinary CLI output, transactional rollback and connection termination. Cleanup
verifies deployment markers and restores the fixture's original public grants
and database comment. Final inspection found no new roles or test schemas.

These results do not prove domain grants/RLS, production Compose deployment,
per-role pool sizing, PgBouncer, backup recovery or a completed metadata cutover.
