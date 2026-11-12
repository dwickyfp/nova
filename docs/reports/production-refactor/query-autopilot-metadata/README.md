# Query Autopilot metadata storage checkpoint

Source parent: `c6a45bac`. Revision 0023 and `AutopilotMetadataRepository`
prepare the thirteen `QUERY_AUTOPILOT_*` tables in PostgreSQL. API, proxy,
scheduler and autopilot worker code is not cut over. See
[the contract](../../../architecture/production-refactor/PG_QUERY_AUTOPILOT.md).

## Local test layout

Migrations must be contiguous. For local runs only, revisions 0017 and 0018
were the real Streams and ML engine files from `feat/production-control-plane`
(`0555b1ad`, not committed here) and 0019–0022 were empty placeholders
(`DO $placeholder$ BEGIN END $placeholder$;`). The placeholders were deleted
before commit. A bare `SELECT 1;` placeholder fails: the migrator runs each
revision as PL/pgSQL, where a result without a destination is an error.

## Results (local device, existing PG17 TLS fixture, no other stack)

Every PG run held `/tmp/nova-metadata-pg.lock` in the same shell command.

- Real PG, the 20 CI-listed modules: 668 passed, 0 skipped (13 min 42 s). At
  start and end, `pgrep` showed no other pytest run, only another session's
  wrapper waiting on the same lock.
- Query Autopilot module: 26 passed, 0 skipped: concurrent policy edits from one
  version (one success, one conflict) and audit rows per version; candidate
  revision CAS under concurrency; invalid proposals refused; an approval only by
  `web`, only from `READY_APPROVAL`, recorded with the approving principal, role
  and context version, not movable, not carried into a new version and not
  restored after withdrawal; `APPLYING` refused without a current approval; jobs
  created once under concurrency, reused idempotency keys refused, requested
  operations attributed to the bound actor, scheduler limited to housekeeping;
  one of four concurrent claimers wins; a retaken lease fences the old worker
  from recording an intent, acknowledging, recording an outcome, heartbeating or
  finishing; finished jobs frozen; actions, outcomes and observations accept
  identical retries and refuse different ones; action acknowledgements must keep
  statement digests, binding and verified object identity; evidence completes
  once, then only expires; retention never deletes decisions or incidents;
  per-workload read, write and delete grants for every role in
  `WORKLOAD_ROLES`; maintenance refusal; import refused outside maintenance and
  keeping legacy rows, timestamps and provenance; an imported running job must
  be retaken, and an imported unleased intent or unbound approval cannot be used.
- Mutation check: making the lease check always pass let a stale worker record
  an action intent; `test_a_stale_worker_cannot_apply_or_record_an_outcome`
  failed ("DID NOT RAISE MetadataConflict"); restored.
- First run defect, fixed before the passing run: the `SELECT 1;` placeholders
  above (fixture setup failed with `MetadataOperationFailed`). The new migration
  itself passed on its first applied run.
- Unit/coverage 10,946 passed (0 skipped); trajectories 358 passed with CI's
  throwaway `SECRET_KEY`/`FERNET_KEY` (without them, two Dataviz trajectories
  fail on the missing signing key, as in any worktree without `.env`);
  scorecard 54/189; `mypy app` clean; Ruff clean on the changed Python files.

Delivered-commit CI is not yet checked. Findings that storage cannot close are
listed in the contract.
