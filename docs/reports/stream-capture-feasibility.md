# Stream capture: feasibility evidence and decisions (2026-11-11)

This report covers WP-00 of `NOVA_STREAMS_TASKS_PRODUCTION_SPEC.md`. For each
fact behind Nova's Stream capture it names the engine probe or test that
establishes it, and it lists what the implementation has not proven. It
replaces the "provider unavailable" status in the 2026-10-05 Streams reports
(`nova-streams-*-20261005.md`), which describe the earlier namespace-only stage.

Engine: StarRocks 4.1.4 with the patched FE (shared-nothing, one BE) and Ranger,
on the local dev stack. User documentation:
[13 — Nova Streams](../sql_docs/13-nova-streams.md).

## Capture strategy

StarRocks has no row-level change feed for Primary Key tables that Nova could
read with exact before-images. Its binlog is not available for this table type
and pin. Nova therefore uses the spec's strategy §5.2(b): it writes the change
itself, through a write fence.

- Every write to a registered source that goes through Nova is executed by the
  capture coordinator (`modules/streams/capture.py`): INSERT, UPDATE, DELETE,
  MERGE (planned as whole-row queries by `sql_frontend/merge.py`) and TRUNCATE
  (journaled as the deletion of every row). The coordinator first reads
  the affected rows under a per-source Redis lock. It then runs one engine
  transaction containing the source DML, the journal rows (before and after
  images, HMAC digest) and the lineage rows (stable `ROW_ID` per key).
- The transaction runs as a dedicated principal, `nova_stream_writer` (role
  `nova_stream_writer_role`). Ranger grants it the source, journal and lineage
  tables per source (`nova-managed/stream-writer/<source_id>`). The caller's
  own write privilege is checked first with Ranger (`role_allows`) for the
  caller's active role, so the writer never widens what the caller may do.
- The commit index `NOVA_SYSTEM.AUDIT_STREAM_SOURCE_COMMITS` records each
  commit's partition version before and after. Every read verifies the engine's
  current version against the last recorded commit. Any difference means a
  write bypassed Nova, and the source becomes `BLOCKED`.

Rejected alternatives:

- Triggers: the engine has none.
- Diffing snapshots: this cannot produce the per-commit order or the
  intermediate images the reducer needs.
- Using `root` or the caller's account for journal writes: `root` violates the
  service-principal rule, and the caller would need write grants on hidden
  tables.

## Engine facts, probed

| Fact | Probe | Consequence |
| --- | --- | --- |
| `BEGIN; UPDATE/DELETE src; INSERT journal; COMMIT` is atomic across tables of one database | fault injected between statements, then ROLLBACK; and a COMMIT then a read | Journal and source commit together, so there is no outbox gap |
| A table can be written only once per transaction, and cannot be read after it was written | engine errors on a second write and on a read after the write | The pre-read happens before `BEGIN`, under the source lock. Each commit writes source, journal and lineage once |
| A transaction across databases is refused (5304), but an earlier statement still commits on COMMIT | probe | The journal and lineage tables live in the source database. Every error path issues ROLLBACK |
| Partition `VisibleVersion` moves +1 per committed write, does not move on a no-op or failed DML, and is reset by TRUNCATE | `SHOW PARTITIONS` before and after | It works as a bypass detector. No-op writes are not journaled. An engine TRUNCATE is never sent for a source: Nova journals a captured DELETE of every row instead, which moves the version by exactly one |
| Dropping and recreating a table changes its table id | `information_schema.tables_config` | A changed table id or column set makes the source `SOURCE_INVALID` |
| An inline typed `(VALUES …) AS t(c…)` works, but derived-table column-list aliases come back lowercased | probe | The Stream relation uses select-list aliases (`METADATA$ACTION`) |
| A labeled INSERT, even an empty one, leaves a `FINISHED` load record with that label | `SHOW LOAD WHERE LABEL = …` | A consuming INSERT is verified by its label before the offset moves |
| Under `EXECUTE AS` impersonation, the load record's user is the login account | probe as `nova_task_worker` | Verification compares against `SELECT USER()` from the same connection |
| The engine checks `SELECT` on a table for a `WHERE 1=0` branch too | Ranger deny probe | A guard branch makes the engine check the caller's SELECT on the source when the Stream is read |

## What the tests and live labs establish

- **Reducer.** The full §7.2 CRUD matrix, for both Standard and append-only
  Streams, plus typed equality, NULLs and order independence
  (`tests/unit/test_streams_core.py`, 31 tests).
- **Runtime.** Contiguous commit indexes, digest checks, bypass and invalid-source
  transitions, the claim and label-verified receipt chain, refusals and
  `HAS_DATA` errors (`tests/unit/test_streams_runtime.py`, 46 tests).
- **Live Stream lab, dev stack, 43/43.** The CRUD matrix on a real table, an
  append-only Stream, lineage, peek, joins and CTEs, independent offsets across
  two Streams, empty and second consumption, every refusal, a direct engine write
  making the Stream blocked, and stream-triggered `EXECUTE TASK`, `WHEN` false,
  `DESCRIBE TASK` and `ALTER TASK RESUME/SUSPEND`.
- **Live trigger lab, 10/10.**
  - A suspended task does not fire.
  - After RESUME, the task fires in about 6 s under the task's schedule account.
  - It does not run again without new data, and runs again when new data
    arrives.
  - An update arrives as a DELETE and INSERT pair.
- **Task lab, 44/44.** The existing task surface still behaves as before.
- **Second round, 28/28** (`stream_lab2`): `SHOW_INITIAL_ROWS` (snapshot read,
  skipped by other streams, netted with later changes, consumed), MERGE with
  update/insert and with delete (captured, source stays READY, defaults for new
  rows), TRUNCATE as deletions, `CREATE OR REPLACE`, `SHOW STREAMS` status, the
  consumptions API and the stream metrics. The first lab (43/43) and the trigger
  lab (10/10) pass again on the same code.
- **Snapshot crash safety.** A snapshot writes its journal rows, then its index
  row; rows above the verified head are deleted under the source lock before
  any commit, so an interrupted snapshot cannot collide with the next sequence
  (`tests/unit/test_streams_completion.py`).

## Limits (by design in v1)

- **Sources.** Only internal, unpartitioned Primary Key tables, with exact column
  types. JSON, ARRAY, MAP, STRUCT, BITMAP, HLL and VARBINARY are refused, because
  their images cannot be round-tripped exactly.
- **Governance.** A source with a Ranger row filter or masking policy is refused.
  A Stream shows deleted and overwritten values, and the spec's §11 proof that
  such policies govern those historical values does not exist yet.
- **Writes.** Only writes made through Nova are captured. `INSERT OVERWRITE`,
  `ALTER TABLE`, partial column lists, `UPDATE … FROM`, `DELETE … USING`,
  partition clauses and explicit transactions are refused on sources. A MERGE
  with a DELETE clause and an update is two commits (delete, then upsert): the
  engine writes a table once per transaction, as an ordinary MERGE of that shape.
  Stream Load, Routine Load and other direct writes cannot be captured, so they
  block the source.
- **Partitioned tables.** A captured commit can move several partitions'
  versions, and the engine does not say which partitions a commit touched, so
  a direct write to another partition in the same instant would be absorbed.
  They stay refused so that "nothing is silently missed" holds.
- **Write size.** At most 50,000 rows per write. Writes to one source are
  serialized by its lock.
- **Recovery.** A blocked or stale Stream has to be recreated
  (`CREATE OR REPLACE STREAM`). There is no force-advance, by design (spec §6.4:
  no reset behind the user's back).
- **Consumption.**
  - Only `INSERT INTO … SELECT` consumes a Stream, one Stream per statement.
  - The sink must be a table where the engine records a load label.
  - `MERGE`, `UPDATE`, `DELETE` and CTAS cannot consume.
- **Retention.** Unconsumed changes are kept `STREAM_RETENTION_DAYS` (14). Past
  that the Stream is `STALE` and its changes are released. Pins are not needed:
  collection only removes commits at or below the lowest READY offset, and
  every read is above its own offset. `HAS_DATA = FALSE` keepalive is not
  implemented (nothing pending never goes stale).
- **Not implemented from the spec.** `AT`/`BEFORE`, Streams on views or external
  tables (out of initial scope, §4), the Snowflake oracle suite (§14.3), and the
  load and crash-matrix gates (§14.4).

## Operations

- Provision the writer once with
  `backend/scripts/provision_stream_writer.py --write-env .env`. The script
  creates the user, the role, its grants and the Ranger role membership, and it
  never prints the password.
- `CREATE STREAM` fails with `STREAM_CHANGE_TRACKING_UNAVAILABLE` while the
  writer is not configured. That is a prerequisite, not a feature flag.
- Journal and lineage tables are hidden (`__nova_stream_*`) from the explorer.
  Journal rows below every Stream's offset are collected on consumption.
