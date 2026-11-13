# Notebook metadata storage checkpoint

Source parent: `8f29ad00`. This checkpoint adds revision 0015 and
`NotebookMetadataRepository` for `CONFIG_NOTEBOOK_RUNTIMES`,
`CONFIG_NOTEBOOK_EXECUTIONS` and `CONFIG_NOTEBOOK_PACKAGES`. Runtime callers
are not cut over; nothing in the running application imports the repository.
See [the contract](../../../architecture/production-refactor/PG_NOTEBOOKS.md).

## What the tests establish

All 614 real-PG tests pass with zero skips on the existing PG17 TLS fixture in
the `docker` project (no other stack started), including 32 Notebook cases:

- R21: two people sharing a notebook each save and restore their own manifest;
  each package change is audited with its owner.
- A worker install and a web removal made at the same moment both survive
  (row-locked modify); stale and duplicate first saves conflict.
- Manifests must be lists of package objects, in Python and in the database.
- Runtime rows keep their identity, never lower their generation, require an
  end time for terminal states and freeze once ended.
- Executions are write-once; an identical retry is idempotent, a different
  retry conflicts, a raw update is refused, and invalid states, cell kinds and
  time order are rejected; executions write no audit rows.
- Rows are personal: another person reads nothing and a worker cannot write
  another person's rows; only the web and notebook logins have grants, and only
  the notebook worker writes runtimes and executions.
- Maintenance blocks writes but not reads; import works only under maintenance
  and keeps legacy rows, including values today's guards would refuse.

A mutation check that let a stale manifest revision through made the stale
write test fail; the code was restored.

## Other checks

Unit/coverage 10,946 passed (0 skipped); 358 evals; scorecard 54/189; mypy;
Ruff on the changed Python files; user-data boundary check. Today's PG server
log window (165 lines) has no credential canaries. Delivered-commit CI has not
been checked.

## Remaining

Wiring into the worker, runtime service, package flows and Superset previews;
durable execution intent with generation fencing; timestamp provenance for
import; rehearsals and the shared cutover. R21 stays open until runtime callers
use the new key.
