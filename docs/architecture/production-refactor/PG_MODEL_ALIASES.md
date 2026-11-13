# Function-alias storage before cutover

Revision `0007_model_aliases.sql` and `llm_functions.repository.ModelAliasRepository`
add actor-bound PG storage for `CONFIG_MODEL_ALIASES`. The running application
still uses the StarRocks service. This checkpoint does not register a UDF, call
a provider, or complete the AI domain's cutover.

## Transaction contract

IDs, names, provider/model references, prompts and raw JSON retain their values.
Source null flags, authors and timestamps remain representable for import;
new writes require explicit boolean state. Original creation fields cannot be
changed by a runtime login. The integer `metadata_revision` supplies CAS, while
updates use the database clock. Deleted IDs remain reserved as tombstones.

One transaction-scoped advisory lock serializes the low-volume administrator
alias, provider and model mutations. It is acquired before any provider/model
row lock, including direct registry calls. A mutation can move a default between
function types and touch
several providers; the shared lock avoids inconsistent ordering among those
configuration operations. A unique partial index independently enforces at most one
non-deleted default per function type. Every affected alias revision advances,
including an old default cleared by another alias's mutation.

Create, replacement and deletion run inside a savepoint owned by the service's
outer transaction. Catching a duplicate or stale revision does not retain a
partial default change. A replacement reports both the old and new function
types, so the future reconciliation service can handle both. Deleting a default
promotes the first active remaining alias in name/ID order. A disabled configured
default remains selected: callers must reject its execution instead of silently
invoking another provider. When no default exists, selection uses the first
active alias in the same deterministic order.

Provider locks serialize alias writes with registry deletion. A configured model
must belong to that provider, remain non-deleted and have the matching model type.
Database triggers also protect direct writes. A provider or model referenced by
a non-deleted alias cannot be tombstoned; a failed provider deletion rolls back
its child-model and credential changes. A service can delete the aliases and
their provider in one enclosing transaction. Inactive configuration can remain
referenced; this does not authorize invocation of inactive providers or models.

## Access and evidence boundaries

The table forces RLS and requires a complete transaction-local actor context.
Only the web login with an active administrator role can mutate it. The read
workloads match the [AI registry](PG_AI_REGISTRY.md); scheduler, notebook and
outbox logins have no alias-table access. Runtime grants prohibit physical
deletion, truncation and rewriting original identity/creation fields. The guard
checks administrator context before looking up references, so a refused writer
receives a permission error without learning reference-validation results.

The privileged fixture import supplies an explicit import actor and preserves
original source fields. It tests representation only; it is not the production
import protocol. Production import still needs the reviewed maintenance,
deployment-lock and epoch procedure.

The storage tests cover concurrent defaults, concurrent CAS, default moves,
rollback, retained identities, provider-deletion races, reference type checks,
and workload permissions on the existing PG17 TLS fixture. The embedding case
checks only a synthetic model reference. It makes no provider call and does not
replace the owner's waived live embedding tests.

## Required before runtime adoption

The prepared [AI configuration service](PG_AI_CONFIGURATION.md) now commits
immutable function definitions, jobs and outbox notifications with these
mutations. Deferred constraints reject runtime writes that omit their intent.
Revision 0009 adds [atomic alias success audit](PG_AUDIT.md), including both rows
in a default move. Live caller and refusal-audit integration remain open.
Native UDF installation still needs
a participant-specific intent and receipt verifier; a lost lease alone never
authorizes another DROP/CREATE. Gateway cache partitioning, revocation, the
unsupported `embed` registration contract (R33), expected revisions in APIs/UI,
and all caller ports remain open. This repository intentionally performs no
external I/O and does not claim those requirements are satisfied.
