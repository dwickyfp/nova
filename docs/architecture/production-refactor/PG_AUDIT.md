# Transactional audit storage

Revision `0009_transactional_audit.sql` prepares `nova_meta.audit_log`. Live
audit writers and history readers still use StarRocks. This storage contract
does not establish a completed audit migration or a production retention policy.
The [verification packet](../../reports/production-refactor/WP-03/audit-commands.json)
records source hashes, all attempts and the final 333-case PG run.

## Events and identity

All 27 legacy columns remain representable, including nullable actor fields and
repeated `log_id` values. The reviewed composite primary key is
`event_time,record_id`; an imported record ID comes from its immutable export
address. A new event carries an explicit aware timestamp and UUID through the
entire operation. `log_id` remains a compatible numeric field, with an identity
sequence for new events. Sequence gaps do not indicate missing committed events.

`common.metadata_audit.AuditRepository` uses the caller's transaction. Replaying
the same composite identity returns the existing row only when every supplied
field, principal, active role, security revision, principal type and writer
login matches. A different payload or context conflicts. It never commits the
outer transaction or retries an uncertain outcome. Callers must retain both
identity components before an operation that might need reconciliation.

New SQL, rewritten SQL and error text use the existing credential redactor and
UTF-8 byte budget. The shared formatting helper imports no engine adapter or
application configuration. Representations omit event content. This is not a general
secret detector: callers must not put credentials into names or other fields,
and stored-secret/provider errors need their existing sanitized error contract.
There is no automatic capture of request headers, provider configuration or
execution-local variables.

## Atomic metadata changes

Preference, provider, model and alias writes produce an audit record in their
own transaction. The record contains the table, object key, revision, action
and original actor, without values, prompts, ciphertext or endpoints. Default
moves record both the newly selected alias and the demoted alias. A later
constraint failure or rollback removes the domain write, registration intent
and success audit together.

A narrowly scoped security-definer trigger writes the reserved metadata fields;
ordinary workload inserts cannot supply those fields or forge a metadata
success event. Constraints also reject partial table/key/revision tuples during
privileged import. The trigger accepts only its four fixed source tables, keeps a
fixed search path and applies RLS. It does not give a workload the owner role.

An audit refusal/failure must be recorded in a separate transaction after the
failed domain transaction has rolled back. The repository test proves this
composition; the live API/domain callers still need to be ported. An unknown
external outcome must retain its uncertainty, not be labeled ordinary failure.

Privileged import is distinguished from runtime sessions explicitly. PostgreSQL
superusers satisfy role-membership checks even without an explicit app grant;
membership alone cannot select the runtime audit path. Imported configuration
does not fabricate user-success audit or registration intent. Revision 0009
also corrects the previous AI consistency trigger's import classification.
The importer must separately validate all definitions and native functions
under maintenance before any metadata epoch becomes active.

## Grants, partitions and remaining work

Every workload can append its own governed events. Reads require the same
principal and active role; web and readonly logins with an active administrator
role may read the complete audit trail. The readonly login cannot append.
Runtime identities cannot UPDATE, DELETE, TRUNCATE, disable triggers, forge
actor fields or read partition tables directly. PostgreSQL RLS does not govern
TRUNCATE, so the missing grant is part of this boundary, as described in the
[PG17 row-security reference](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

The installer creates the current UTC month and two future monthly partitions,
plus a default partition that preserves older or otherwise uncovered events.
Partition keys and primary-key shape follow
[PG17 partitioning constraints](https://www.postgresql.org/docs/17/ddl-partitioning.html).
The parent routes normal reads/writes; leaves have no runtime grants and force
RLS. Partition maintenance, default-partition redistribution and retention
after verified export/backup remain open. No retention deletion is implemented.

Bootstrap and runtime admission also refuse privileged parameter SET grants
and all ALTER SYSTEM grants. This includes `session_replication_role`, which
could otherwise disable normal triggers. The check includes inherited/public
privileges and conservatively treats unknown parameter grants as privileged.
Admission also requires effective `session_replication_role=origin`. Revoking
a SET grant does not reset a pooled backend's previous setting; checking the
grant alone would admit a backend with its normal triggers disabled.
It uses PostgreSQL's [parameter ACL catalog](https://www.postgresql.org/docs/17/catalog-pg-parameter-acl.html)
and effective privilege inquiry, without silently repairing operator changes.

Import orchestration still needs source timestamp provenance, stable duplicate
identities, complete redaction review, preservation of sensitive historical
source evidence under restricted encryption, sequence positioning beyond the
imported numeric IDs, and counts/key/digest validation. The synthetic duplicate
import test proves representation only. All audit/history/monitoring/lineage
caller ports, remaining domain audit hooks, retention/export, backups and
rehearsals remain release gates. History and lineage projections must apply the
applicable historical-data authorization before returning statement fields;
ownership of an audit event does not prove current access to its referenced data.
