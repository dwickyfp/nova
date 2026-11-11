# Durable job and outbox storage

Revision `0005_durable_jobs.sql` and `app/common/durable_jobs.py` implement the
PostgreSQL queue storage contract. The current Task, agent, ingestion, notebook,
ML and automation callers still use their existing runtime paths. This revision
does not switch a domain or publish a metadata epoch. Participant adapters,
immutable domain definitions, scheduler leadership, cancellation requests and
runtime dispatch remain migration work.

## Transaction ownership and identity

The domain service owns the actor-bound transaction. It writes its immutable
definition and domain state, then enqueues a reference to that definition in the
same transaction. The queue contains an opaque definition UUID, its version and
SHA-256 digest. It never holds SQL, prompts, provider responses, URLs, passwords
or arbitrary JSON. Original external object IDs stay in the owning domain;
these UUIDs identify new internal records.

The job captures principal, one active role, principal type and security-context
version from transaction-local settings. A duplicate idempotency key must have
the same owner context and definition. A conflicting request returns 409, even
if a trusted worker can inspect the original job. Terminal jobs remain present;
runtime roles cannot delete or truncate them or edit their definitions. Future
archival must preserve the unique key and permanent receipt identity.

Workers use their own workload login and a SERVICE actor for queue control.
Forced RLS permits mutation only in that workload's queue. It does not give the
worker permission to execute the caller's data operation. The participant adapter
must revalidate the captured caller context through Nova's existing governed
execution path. A service credential must never replace a revoked caller.

## Claims and external effects

Claims use database time and `FOR UPDATE SKIP LOCKED`. Each acquisition increments
a fencing token; heartbeats and state updates require the holder UUID, token,
revision, expected state and an unexpired lease. Repositories require an existing
transaction and use savepoints for multi-statement mutations. They neither commit
the service transaction nor retry uncertain commits.

For an external effect, the adapter must commit PREPARED and its immutable intent
before sending the request. The operation UUID is derived from job ID and attempt.
The intent records participant and request digest; the actual authorized request
belongs in the immutable domain definition. Multiple effects require separate
jobs with explicit domain dependencies.

An expired LEASED job with no armed intent can be acquired for execution. An
expired PREPARED, SUBMITTED, VERIFYING or BLOCKED job can only be acquired in
VERIFYING with the same attempt and operation ID. It cannot prepare or submit
again. An expired COMMITTED job retains its verified outcome and can finish its
metadata publication.

An unresolved intent holds a unique resource digest until verified completion or
proven failure. A second job targeting that resource cannot prepare. Domain
adapters must derive this digest from the canonical participant/resource identity,
including shared resources across callers; a random digest would defeat the fence.

The participant verifier supplies an opaque evidence reference and result digest.
VISIBLE moves a job to COMMITTED. ABSENT means a final proof that the operation
did not commit and cannot still commit: a missing lookup result alone is
insufficient while an old request or worker can remain in flight. This judgment
belongs to the participant-specific verifier and still needs engine/service fault
acceptance. The storage layer cannot infer it from a lease or HTTP status.

A missing or ambiguous outcome moves to BLOCKED. FAILED requires an ABSENT
receipt; only an explicitly retryable failure may return to PENDING after a
recorded delay. Every attempt keeps its immutable intent and receipt. Receipt
insertion and outcome changes commit together. Completion also uses the domain
transaction so a failed domain update cannot leave a completed queue record.
Only jobs declared to have no external effect can use the internal commit/fail
path. Cancellation is currently limited to a worker's unprepared live lease.

## Outbox and recovery

Enqueue and explicit retry insert an outbox event atomically. A deferred database
constraint refuses a PENDING job without its corresponding wakeup. Notification
data contains only event ID and job ID. Workers poll PG regardless of whether a
wakeup arrived, and load definitions and authorization from their authoritative
owners before executing.

The outbox workload claims events with a separate token and database deadline.
It publishes after committing the claim and acknowledges in a new transaction.
A lost acknowledgment causes another notification with the same event ID. Queue
claiming, rather than Redis delivery, decides whether work may execute. A failed
publication records a bounded retry delay; it does not roll back the job.

No Redis publisher loop or production runtime is wired by this storage change.
The tests exercise polling without notifications and lost publisher ACKs at the
repository boundary; they do not claim a real Redis outage or a worker-kill test
against StarRocks, Superset, OpenSandbox or MCP.

## Validation

Real-PG cases in `tests/integration/test_metadata_jobs.py` cover atomic submission,
concurrent dedupe/claims, SKIP LOCKED, actor and workload boundaries, stale tokens
and revisions, each expired armed state, immutable history, resource conflicts,
receipt rollback, explicit retry, cancellation limits and outbox recovery.
Controlled unit cases reject credential-bearing or ambiguous envelope fields.
Results and retained failures are recorded in
[WP-08 evidence](../../reports/production-refactor/WP-08/README.md).

The next delivery must connect an actual domain definition, participant verifier
and dispatcher, then test external commit ambiguity and stale in-flight workers.
Passing storage tests alone does not establish exactly-once external effects.
