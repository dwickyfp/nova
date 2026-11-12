# Task orchestration metadata port

Revision 0016 prepares `CONFIG_TASKS`, `CONFIG_TASK_EDGES`,
`CONFIG_TASK_GRAPH_RUNS`, `CONFIG_TASK_RUNS` and `CONFIG_TASK_ROLE_BINDINGS`
in `nova_meta`, with `TaskMetadataRepository` as the prepared replacement for
the `NOVA_SYSTEM` paths of `TaskOrchestrationRepository`. Running scheduler,
worker and API code still uses `NOVA_SYSTEM`; nothing imports the repository.

## Access

Task metadata is control-plane state. The owning service (and the engine, for
data) authorizes callers, so PG access is per workload through a bound actor
context, never anonymous:

| Table | Read | Write |
| --- | --- | --- |
| Definitions | web, query, scheduler, task | web, query; the task worker may only change the failure counter |
| Edges | same | web, query |
| Graph runs | same | web, scheduler, task |
| Node runs | same | task worker |
| Role bindings | same | web |

Other workloads have no grant. Definitions and edges can be deleted (DROP TASK);
run history cannot be deleted by any workload.

## Wave B gaps closed in the new store

- **Version CAS.** A definition change names the version it read and returns the
  row that transaction wrote; two concurrent edits from one version produce one
  success and one conflict. A raw definition change without a new version is
  refused.
- **Atomic failure counter.** One `UPDATE … RETURNING`; six concurrent failures
  return 1–6. The counter is bookkeeping: it does not change the version or
  write an audit row.
- **Null-safe scope.** Runtime tasks are unique by `(database, schema, name)`
  with null scopes compared as equal values (`NULLS NOT DISTINCT`), and lookups
  use `IS NOT DISTINCT FROM`. Imported duplicates are kept and returned together
  so a caller can see the ambiguity instead of a silent pick.
- **Created once.** Graph runs (by id) and node runs (by the same deterministic
  UUIDv5 as the source) are created once; a racing creator gets the existing row,
  never a reset one.
- **Lease fencing.** A claim moves a run and increments its `lease_generation`.
  Heartbeats, transitions and node outcomes require the current generation, so a
  worker that lost its lease (for example after its node was marked abandoned and
  retaken) can no longer heartbeat or finish. Terminal runs and nodes are frozen,
  and a claim on a terminal row reports "not claimed". Imported running nodes
  carry no live lease and must be retaken.

Definition, edge and role-binding changes write a success audit row; run rows
are execution history, as in the source, and are not audited. Edge kinds,
schedule kinds, overlap policies, handlers, lifecycle states and run states are
checked against the values the code writes today.

## Remaining task work

- Wire the repository into the scheduler, worker, reconciler, DDL lowering and
  router at cutover, with each worker holding the lease generation it claimed.
- Persist graph creation together with its outbox intent, connect durable jobs,
  and add receipts and operator reconciliation for uncertain node outcomes.
- Timestamp provenance (engine `NOW()` versus supplied heartbeat values) and the
  import of duplicate names, orphan references and legacy null scopes.

Validation is recorded in
[the Task metadata checkpoint](../../reports/production-refactor/task-metadata/README.md).
