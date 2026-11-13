# Stream metadata storage checkpoint

Source parent: `ed4afed3`. This checkpoint adds revision 0017 and
`StreamMetadataRepository` for the eight Stream control tables
(`CONFIG_STREAMS`, `CONFIG_STREAM_DEFINITIONS`, `CONFIG_STREAM_SOURCES`,
`AUDIT_STREAM_CLAIMS`, `AUDIT_STREAM_NAMESPACE_OPERATIONS`,
`AUDIT_STREAM_CONSUMPTION_OPERATIONS`, `AUDIT_STREAM_CONSUMPTIONS`,
`AUDIT_STREAM_SOURCE_COMMITS`). Runtime callers are not cut over; nothing in
the running application imports the repository. The engine journal/lineage
tables and engine observers stay in StarRocks. See
[the contract](../../../architecture/production-refactor/PG_STREAMS.md).

## What the tests establish

`tests/integration/test_metadata_streams.py` (31 cases) on the existing PG17
TLS fixture, with no other stack started:

- Five concurrent claims of one key produce exactly one winner; every caller
  reads the same winner. A claim key that is not the derived digest is refused.
- Only the namespace claim winner publishes, only with its prepared facts; a
  lost-ACK republish returns the same row. Generations publish in order and
  cannot be updated or deleted. Create and drop write one audit row each.
- Namespace operation facts, definitions, the commit index and consumption
  operations accept an identical retry and refuse a different one.
- The commit index refuses gaps, version breaks and version jumps; two
  concurrent writers of one sequence produce one winner and one conflict.
- Source status moves forward under a revision CAS (two concurrent changes from
  one revision: one wins); READY needs the baseline; DROPPED is final; one live
  registration per table, case-insensitively; status reasons are cut to 500
  bytes on a character boundary.
- Consumption events repeat their prepared operation exactly, need the winning
  submit/cancel claim, and keep terminal receipts identical: a duplicate
  delivery is idempotent, while a different receipt, a FAILED after a commit,
  malformed receipts (wrong outcome, no reason, negative identity, extra
  fields) are refused. A cancel winner blocks submission for good.
- Offset receipts form one unbroken chain: a gap, a shared start or an overlap
  is refused, commit evidence alone does not move the offset, and two
  concurrent offset receipts from one start produce one winner.
- Every role in `WORKLOAD_ROLES` was checked: the seven SQL paths and the
  scheduler read and write what PG_STREAMS.md lists; the scheduler cannot
  register sources, index commits or add definitions; `ml`, `ingestion`,
  `maintenance` and `outbox` read and write nothing; no workload deletes. An
  unbound login reads no rows.
- Maintenance blocks writes but not reads. Import works only for the migrator
  under maintenance and keeps legacy rows today's guards would refuse
  (non-hex ids, READY without baseline, generation gaps, overlapping
  receipts); the read-side chain check still blocks those receipts, and runtime
  changes to imported rows still follow the transition rules.

Mutation check: disabling the offset overlap/shared-start guard in 0017 made
`test_offset_receipts_form_one_unbroken_chain` (did not raise the conflict)
and `test_concurrent_offset_receipts_have_one_winner` (two winners) fail; the
file was restored and the module passes.

## Other checks

All 20 CI-listed real-PG modules: 673 passed, 0 skipped, run alone on the
fixture under the shared lock (process list sampled four times during the run).
Unit suite with coverage 10,946 passed (0 skipped); 358 trajectory evals;
scorecard 54/54 scenarios, 189/189 checks; `mypy app` clean; Ruff clean on the
changed Python files. Delivered-commit CI has not been checked.

## Remaining

Wiring into the Stream service, SQL intercept, capture coordinator, scheduler
maintenance and task triggers; the import transform (JSON strings, DATETIME
provenance) and an operator path for imported inconsistent receipts; the Wave B
schedule/WHEN and crash matrix; the shared cutover.
