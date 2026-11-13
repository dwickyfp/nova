# PostgreSQL Stage registrations

Revision `0012_stage_registrations.sql` prepares `nova_meta.config_stages`.
The current Stage API, SQL adapters, Explorer and notebook RPC still use their
existing StarRocks metadata paths. This is a prepared domain port, with no
dual-write, runtime selector or partial authority switch.

## Identity and mutation

All eight source columns remain representable, including opaque IDs, nullable
owner/time and an empty legacy prefix. Import adds `namespace_kind=LEGACY`
explicitly; it does not claim that historical mappings were authorized or safe.
Source byte widths are enforced in addition to PostgreSQL character widths.
Timestamp conversion still requires the recorded source provenance.

New registrations use a server-generated UUID and `nova/stages/<UUID>`.
Custom mappings require active ACCOUNTADMIN and a normalized relative folder.
The service verifies the exact named connection without resolving credentials.
The database independently checks new identity/prefix correspondence, mapped
path safety and the active administrator requirement. Name, scope, connection,
prefix, original owner and creation time are immutable to workload logins.

A unique live `(database_name,schema_name,name)` key preserves exact-case
resolution and rejects duplicate registrations. Deletion requires the current
positive `metadata_revision`, advances it once, and retains a tombstone. The
primary key cannot be reused, and recreating a logical name allocates a new
folder. Deleting a registration has no file-deletion effect. Competing writes
produce one winner and a conflict; they do not retry an external operation.
Successful mutations and their redacted metadata audit record share one
transaction. An audit failure rolls back the registration.

## Governed scope

Stage permissions remain schema-governed by the existing Ranger service, not
owner ACLs. `StageMetadataService` checks that its immutable `SecurityContext`
matches the UoW principal, active role, context version, principal type and login before
and after the policy call. The registration create/delete path retains the
current schema DELETE requirement. File consumers request the applicable read,
write or delete action before obtaining the payload.

A fixed SECURITY DEFINER discovery function returns only ID, name, database and
schema to explicit backend consumers. It exposes no connection, prefix, owner,
timestamp or credential. It requires an actor and matching workload identity;
runtime identities cannot assume the owner role. Discovery is internal input to
Ranger authorization, not a public listing endpoint.

After Ranger allows the action, transaction-local database/schema/action fields
permit the corresponding payload under forced RLS. Missing context or scope
returns no payload. A savepoint bounds this scope to the operation and rolls it
back on a caught error or cancellation. Subsequent commands and pooled requests
must authorize again. These fields enforce the trusted backend contract; they
are not cryptographic policy decisions or protection against a compromised
backend holding the workload login. User SQL never receives these credentials.

| Workload | Stage access | Inspected consumer |
| --- | --- | --- |
| web | Discovery, authorized payload, registration insert/tombstone | Stage routes; Explorer |
| query, proxy | Discovery and authorized payload | Shared SQL Stage resolver and completion |
| agent | Discovery and authorized payload | Shared query tools and Studio SQL |
| task | Discovery and authorized payload | SQL/Python task query execution |
| ml | Discovery and authorized payload | `prepare_stream_sql` in ML input preparation |
| notebook controller | Discovery and authorized payload | Governed notebook Stage RPC and SQL |
| automation | Discovery and authorized payload | Existing automation turn invokes the shared agent/query path |
| scheduler, ingestion, maintenance, outbox, readonly, backup SQL | No discovery or payload grant | No direct Stage payload consumer established for these identities |

The notebook grant belongs to its controller, never the Python sandbox. Runtime
separation must revisit the matrix when ownership moves between processes.
Physical backup privileges are separate from ordinary SQL access.

## Import and remaining integration

Only the migrator acting as the schema owner can import, repair or reset this
table, during maintenance and under the exclusive deployment barrier. Legacy
blank fields and original nullable values survive; conflicting identities or
logical names fail the import transaction instead of being coalesced. Workload
writes take the shared barrier and also refuse maintenance when issued directly.

Before cutover, port every StageService reader/writer, SQL Stage resolution,
Explorer summaries and ID resolution, ObjectService schema/tree queries,
QueryService schema/completion/file listing, notebook RPC and ML preparation.
Preserve ambiguity handling, all-scopes authorization before secret resolution,
legacy prefix fallback, refusal audit and session/delegation revalidation.
Registration storage tests do not prove those final runtime paths, immutable
file-effect receipts, historical namespace provenance or a completed import.

Verification evidence belongs in
[the Stage PG packet](../../reports/production-refactor/stage-pg/README.md).
Public `/docs` behavior has not changed in this prepared-store revision; the
current Stage documentation and live namespace correction remain described in
[the runtime boundary review](STAGE_REGISTRATION_BOUNDARY.md).
