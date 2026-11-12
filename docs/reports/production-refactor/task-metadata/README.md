# Task metadata storage checkpoint

Source parent: `1bf472cb`. Revision 0016 and `TaskMetadataRepository` prepare
the five task orchestration tables in PostgreSQL. Runtime scheduler, worker
and API code is not cut over. See
[the contract](../../../architecture/production-refactor/PG_TASKS.md).

## Results (local device, existing PG17 TLS fixture, no other stack)

- Real PG: 642 passed, 0 skipped, including 28 Task cases: concurrent edits
  from one version (one success, one conflict), raw definition change without a
  version refused, six concurrent failure increments returning 1–6 without a
  version change or audit row, null-safe scoped names, invalid definitions and
  edges refused, graph and node runs created once under concurrency and never
  reset, stale graph and node leases unable to heartbeat, transition or finish,
  terminal rows frozen, role bindings revisioned and attributed, per-workload
  grants for every role in `WORKLOAD_ROLES`, run history undeletable,
  maintenance refusal, and maintenance-only import keeping duplicate names and an
  unleased running node that must be retaken.
- Mutation check: letting a stale lease finish a node made the fencing test fail;
  restored.
- First run defects in the new migration, fixed before the passing run: one
  audit expression referenced fields some tables lack (SQLSTATE 42703), and a
  claim on a terminal run raised instead of reporting "not claimed".
- Unit/coverage 10,946 passed (0 skipped); evals 358; scorecard 54/189; mypy;
  Ruff on changed Python files.

The code was committed locally as `ed4afed3` before the full suites finished,
so that parallel worktree sessions could start from revision 0016; it was
pushed only after the results above. Delivered-commit CI is not yet checked.
