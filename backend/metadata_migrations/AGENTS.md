# Metadata migration guide

Inherit the [root](../../AGENTS.md) and [backend](../AGENTS.md) contracts.
Read this guide for PG migrations and for callers of `core/metadata_db.py`,
`core/metadata_migrations.py`, `core/metadata_bootstrap.py` and metadata import
code, even when the edited file is outside this directory.

## Establish authority before editing

Read the [acceptance contract](../../docs/architecture/production-refactor/ACCEPTANCE.md),
[ownership matrix](../../docs/architecture/production-refactor/METADATA_OWNERSHIP.csv),
[source calls](../../docs/architecture/production-refactor/SYSTEM_CALLS.csv),
[column transforms](../../docs/architecture/production-refactor/COLUMN_TRANSFORMS.csv)
and the owning domain's review. Inspect its actual schema and every writer,
including bootstrap, SQL/proxy, API, workers, import, repair and retention paths.
Use `common/nova_system.py`, `docker/init-nova.sql` and domain DDL as source
evidence, not a generic table template.

Record source and target authority separately. A prepared PG repository or
fixture does not authorize a production caller switch. Port callers through the
approved all-domain cutover; do not add backend selection flags, dual-write or
StarRocks fallback. Keep new work within the existing domain owner and the one
async metadata pool/UoW boundary.

## Transactions, identities and effects

- Repositories receive an explicit actor-bound PG transaction from the service.
  Bind principal, one active role, login identity and security-context version;
  preserve explicit service-principal context for scheduled work. Use local
  transaction settings and clear narrower authorization scopes on success,
  failure and cancellation. Reused/pooler connections must not inherit authority.
- Application workload logins have explicit grants and forced RLS where
  applicable. They cannot own schema objects, migrate, assume owner roles or
  bypass RLS. Audit any fixed SECURITY DEFINER projection: restrict its callers,
  search path, returned fields and operation, and validate the bound actor.
- RLS protects control metadata. It does not replace Ranger checks on user
  data. Shared-file membership and active data-role authorization have different
  contracts; preserve the owning domain's distinction.
- Use constraints and revision compare-and-swap for concurrent changes. Return
  revision conflicts as 409 and maintenance write refusal as 503 through the
  existing exception/API contracts. Do not silently overwrite a stale revision.
- Commit domain state, immutable definition, job/outbox and success audit
  together when the operation requires them. Repositories do not commit the
  caller's outer transaction or retry an uncertain commit.
- Persist exact effect intent before external mutation. Keep receipts and
  lease/fencing checks tied to that intent. An unknown outcome enters explicit
  verification or blocked recovery; lease expiry is not permission to replay.
  A PG transaction alone cannot make object storage, StarRocks or provider
  effects atomic. Preserve Stream journals that belong in the data transaction.
- Preserve encrypted values and secret references through the existing owner.
  SQL errors, manifests and test artifacts must not expose values or credentials.

## Schema and import delivery

1. Add the next ordered migration; never edit an applied revision or its
   checksum. Preserve logical table names and opaque IDs; physical names are
   lowercase in `nova_meta`. Define types, keys, references, uniqueness,
   revision/tombstone behavior, grants/RLS and audit with the owning semantics.
2. Use the one-shot migrator. Pending DDL, ledger checksums and schema version
   commit atomically. Installation does not activate the metadata epoch. Keep
   owner/migrator credentials out of application processes and startup hooks.
3. Implement explicit import conversions and verify source timestamp provenance,
   text/ciphertext, nullable values, key sets, duplicates and object receipts.
   Preserve historical rows and IDs. Emit a redacted conflict manifest for
   unresolved ambiguity; never deduplicate or infer missing history to pass.
4. Port every real caller and writer, including maintenance and recovery, and
   validate compatibility. Prepared stores and prepared caller ports remain
   labelled as such until normal runtime uses the verified target authority.
5. Rehearse twice, including interrupted import and recovery. At cutover freeze
   every writer, drain/checkpoint work, export/import the final snapshot and
   verify counts, key sets, digests and semantics. Publish the epoch only after
   validation, smoke-test, then reopen writes.
6. After PG accepts new writes, recover from PG and associated receipts. Never
   switch back to the stale StarRocks metadata copy. Update ownership, storage
   grants, traceability, risks and operational/docs contracts with the result.

## Validation

Use the backend gates and CI's real PG17 suite, including the pinned transaction
pooler where applicable. Read fixture setup/teardown first and follow the
[Docker reuse rules](../../docker/AGENTS.md). A fixture's disposable label and
ownership guard do not authorize dropping a shared schema or rotating live roles.

Cover fresh install, upgrade, checksum/epoch mismatch, rollback, RLS and direct
SQL grant refusal, actor/login/role changes, cross-user access, concurrent CAS,
maintenance, cancellation and pool reuse. Test imports against the real schema;
retain failed attempts and explicit skips. Add effect crash/receipt tests where
the domain mutates another system. Use patched-FE/Ranger acceptance for affected
user-data paths; PG fixtures cannot establish that boundary.

When adding a metadata suite, register it in CI's explicit test selection.
Do not call a collection with zero cases or skipped prerequisites a pass.
