# Stream control metadata port

Revision 0017 prepares the eight Nova Stream control tables in `nova_meta`:
`CONFIG_STREAMS`, `CONFIG_STREAM_DEFINITIONS`, `CONFIG_STREAM_SOURCES`,
`AUDIT_STREAM_CLAIMS`, `AUDIT_STREAM_NAMESPACE_OPERATIONS`,
`AUDIT_STREAM_CONSUMPTION_OPERATIONS`, `AUDIT_STREAM_CONSUMPTIONS` and
`AUDIT_STREAM_SOURCE_COMMITS`. `StreamMetadataRepository`
(`backend/app/modules/streams/metadata_repository.py`) is the prepared
replacement for the `NOVA_SYSTEM` control paths of `ClaimRepository`,
`StreamRepository`, `StarRocksConsumptionJournal` and the control half of
`SourceRegistry`. Running code still uses `NOVA_SYSTEM`; nothing imports the
repository until the all-domain cutover.

## What stays in the engine

Per the Wave B review, these are Nova control tables only. Out of scope and
staying in StarRocks:

- the per-source journal (`__nova_stream_changes_*`) and lineage
  (`__nova_stream_lineage_*`) tables, which must commit with the source/target
  data in the engine transaction and are written only through `writer_conn`;
- engine observations: `SourceRegistry.engine_identity`, `partition_versions`,
  `columns`, `StreamService._table_exists` and `TargetCommitVerifier`
  (`information_schema.loads`, `SHOW TRANSACTION`).

No PG transaction claims to commit engine data. Recovery verifies engine
evidence first, then records the matching control receipt here.

## Access

Stream statements run through the shared SQL executor, so every SQL path can
create, drop, read, capture into and consume a stream. The scheduler runs
stream maintenance (publishing STALE generations, journal collection) and
stream-triggered task planning, whose `verify` can mark a source BLOCKED.

| Table | Read | Insert | Update |
| --- | --- | --- | --- |
| Sources | SQL paths, scheduler | SQL paths | SQL paths, scheduler (status only, revision CAS) |
| Commit index | SQL paths, scheduler | SQL paths | none |
| Definitions | SQL paths, scheduler | SQL paths | none |
| Claims | SQL paths, scheduler | SQL paths, scheduler | none |
| Namespace operations | SQL paths, scheduler | SQL paths, scheduler | none |
| Generations (`config_streams`) | SQL paths, scheduler | SQL paths, scheduler | none |
| Consumption operations | SQL paths, scheduler | SQL paths | none |
| Consumption events/receipts | SQL paths, scheduler | SQL paths | none |

"SQL paths" are `web`, `query`, `proxy`, `agent`, `task`, `notebook` and
`automation`. `ml` plans streaming input with an empty planning context (no
Stream name resolution), and `ingestion`, `maintenance` and `outbox` have no
Stream consumer; they get nothing. No workload deletes Stream metadata. Every
policy requires a bound actor whose service identity is the session login.

## Rules the database enforces on runtime writes

- **Immutable facts.** Claims, namespace operations, generations, definitions,
  commit index rows, consumption operations and consumption events are
  insert-only. An identical retry (a lost ACK) is accepted without a second
  row; a different fact for an existing identity is refused as a conflict.
- **Claims.** The key must be the SHA-256 of `resource:epoch:generation`. The
  first claim of a key wins; a later claimant inserts nothing and reads the
  winner, which is evidence for verification, never permission to submit.
- **Generations.** Published in order (`max + 1`, starting at 0), only by the
  namespace claim winner and only with the facts it prepared (the operation
  digest binds every published value).
- **Commit index.** Seq 0 is the baseline (`version_before = version_after`,
  no rows). Each later commit starts at the previous `version_after` and moves
  the version by 0 (snapshot) or 1 (captured write). Gaps are refused.
- **Sources.** One live (REGISTERING/READY) registration per table,
  case-insensitively. Status moves forward under a revision CAS: READY only
  from REGISTERING with a baseline, BLOCKED only from READY, DROPPED is final.
- **Consumption events.** The event id is the source's
  `operation_id(consume_id, state)`. Each event repeats its prepared operation
  exactly. TARGET_SUBMITTED, TARGET_COMMITTED, OFFSET_COMMITTED and FAILED
  need the submit claim winner; CANCELLED needs the cancel claim winner.
  Terminal receipts of one consumption must be identical and well formed
  (outcome, reason, nonnegative integer engine identities, nothing else).
- **Offsets.** OFFSET_COMMITTED follows the TARGET_COMMITTED receipt. Under a
  per-stream lock it may not overlap or share a start with another offset
  receipt, and it must start at the creation cursor or where another receipt
  ended. `committed_cursor` keeps the read-side chain check (gap, overlap,
  same start → `STREAM_CONSUMPTION_BLOCKED`), which also covers imported rows.
  It is slightly stricter than the source: two receipts from the same start
  block even when they end at the same offset, as the source docstring says.

Publishing a generation and adding a definition write a success audit row.
Claims, operation facts, receipts, the commit index and source status are
execution evidence and are not audited, as in the source (source BLOCKED /
SOURCE_INVALID keeps its existing application ERROR audit).

## Import

Only `nova_meta_migrator` acting as `nova_meta_owner` under maintenance can
import, and imported rows keep their values and timestamps with
`provenance='imported'`, including rows today's rules would refuse. Runtime
changes to imported rows (source status) still follow the transition rules,
and runtime generations must continue in order after imported ones.
Key/column lists and receipts move from JSON strings to `jsonb`.

## Remaining stream work

- Wire the repository into the Stream service, SQL intercept, capture
  coordinator, scheduler maintenance and task triggers at cutover; replace the
  StarRocks label/`WHERE NOT EXISTS` idioms with these inserts.
- The import transform (JSON parsing, DATETIME to `timestamptz` provenance)
  and an operator path for imported overlapping or gapped receipts.
- The schedule-and-WHEN combinations and the crash matrix from Wave B remain
  unverified.

Validation is recorded in
[the Stream metadata checkpoint](../../reports/production-refactor/stream-metadata/README.md).
