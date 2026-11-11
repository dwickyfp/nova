# Task and Stream persistence review

This is source classification and a migration contract, not an implemented PG
Task/Stream port. Other wave B domains still require review.

## Task repository

`TaskOrchestrationRepository` targets five fixed tables: `CONFIG_TASKS`,
`CONFIG_TASK_EDGES`, `CONFIG_TASK_GRAPH_RUNS`, `CONFIG_TASK_RUNS` and
`CONFIG_TASK_ROLE_BINDINGS`. `get_engine_timezone` is the exception: it observes
the engine session timezone and belongs in the restricted engine adapter.
Dynamic UPDATE columns come from `_UPDATABLE_COLUMNS`; the dynamic graph query
branches only select these fixed metadata tables.

Preserve task IDs, deterministic node-attempt UUIDs and legacy graph identities.
Edges currently name their endpoints rather than reference task IDs; resolution
must include database/schema scope and explicitly handle legacy null scopes.
The MySQL null-safe comparison becomes a deliberate PG null-safe comparison,
not ordinary equality. Validate ambiguous names, duplicate logical occurrences
and orphan references before adding constraints.

Current writes use engine `NOW()`, except explicitly supplied heartbeat/finish
values. Those values need producer-level provenance before timestamp conversion.
Task updates increment `version` without an expected-version predicate. PG
updates must CAS the expected version and return the actual new row from that
transaction. Failure counters need an atomic UPDATE/RETURNING rather than a
separate read that can observe another writer's increment.

Graph insertion and Redis publication are separate operations today. A guarded
engine INSERT and deterministic ID do not prove the publish survived a crash.
The PG transaction must persist immutable task definitions, graph/node records,
idempotency facts and outbox intent together. Scheduler ownership and worker
claims require monotonically fenced updates. The existing state-based UPDATEs
do not fence a stale worker holding an earlier lease.

The uncertain-node hold introduced in this branch prevents known abandoned
nodes from replaying or releasing overlap. It does not implement receipt lookup,
new recovery states or operator reconciliation. A missing native task trace is
not proof that target DML failed.

## Stream control metadata versus engine journals

`ClaimRepository`, `StreamRepository` and `StarRocksConsumptionJournal` receive
an injected executor whose normal default is `db.execute_system`. Their SQL was
missing from a scan of direct method invocations. The inventory now records
indirect candidates as well as their adapter bindings; a candidate still needs
semantic review.

These three repositories address Nova control tables only:
`AUDIT_STREAM_CLAIMS`, `CONFIG_STREAMS`,
`AUDIT_STREAM_NAMESPACE_OPERATIONS`, `AUDIT_STREAM_CONSUMPTION_OPERATIONS` and
`AUDIT_STREAM_CONSUMPTIONS`. Their immutable operation facts, claim digests,
epoch/generation identities and exact receipt checks must survive the PG port.
The class name `StarRocksConsumptionJournal` must not be mistaken for the
source-table transaction journal that must remain in StarRocks.

`SourceRegistry` mixes control metadata with engine observations. Its
`engine_identity`, `partition_versions` and `columns` methods query the engine's
schema/version metadata. Its source registry, commit index and stream
definitions belong in PG. The `_execute` pass-through therefore cannot be moved
wholesale to a PG connection.

`writer_conn` and `writer_query` operate as the configured Stream writer. The
source row images, journal rows and lineage proof must commit with source/target
data in the existing engine transaction. Recovery verifies that evidence before
publishing the corresponding control receipt in PG. No PG transaction claims
to commit StarRocks data atomically.

Offset publication requires the same verified immutable consumption identity.
The current `committed_cursor` checks an unbroken chain and refuses conflicting
or overlapping receipts; preserve that behavior. A schedule that is not due or
a false WHEN predicate cannot consume data or advance an offset. The new
schedule-and-WHEN combinations and crash matrix remain unverified.
