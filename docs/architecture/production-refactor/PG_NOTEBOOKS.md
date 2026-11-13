# Notebook metadata port

Revision 0015 prepares `CONFIG_NOTEBOOK_RUNTIMES`, `CONFIG_NOTEBOOK_EXECUTIONS`
and `CONFIG_NOTEBOOK_PACKAGES` in `nova_meta`, with
`NotebookMetadataRepository` as the prepared replacement for
`NotebookRepository`. It is storage preparation for the all-domain cutover:
the running worker and API still use `NOVA_SYSTEM`, and nothing in the running
application imports the new repository.

## Ownership

Every row belongs to one person, the bound principal. A shared notebook never
shares a runtime, an execution history or a package manifest, which keeps the
per-notebook, per-person runtime scope from the Wave D review.

- Reads: the web and notebook workloads, own rows only.
- Runtimes and executions: written by the notebook worker as the row owner.
- Packages: written by the web (removal) or the worker (install), as the owner.
- Every other workload login has no grant. The migrator imports only under
  maintenance and the exclusive deployment barrier.

## Package manifests (R21)

The source table keyed manifests by `notebook_id` alone while every reader
filtered by person, so two people sharing a notebook overwrote each other's
manifest. The PG key is `(notebook_id, user_name)`. A two-person regression
saves and restores each manifest independently.

A manifest is a JSON list of package objects, checked in Python and by the
database. `replace_packages` saves only at the expected revision (0 creates);
`modify_packages` applies a change under the row lock, so a worker install and
a web removal at the same time both survive. Package changes write a success
audit row with the notebook and person; names and versions are not copied.

Import keeps the surviving source row with its owner. A manifest that was
already overwritten in the source cannot be reconstructed.

## Runtimes and executions

A runtime row has a fixed identity, a generation that never decreases, and the
recorded states `READY`, `FAILED`, `STOPPED` and `LOST`; the last three must
carry an end time, and an ended row never changes again. Executions are
write-once: there is no update grant, an identical retry is accepted, and a
retry with different content is a conflict. Finished states (`succeeded`,
`failed`, `cancelled`, `skipped`, `timeout`), cell kinds and time order are
checked. Runtime and execution rows are operational history the source never
audited, so they write no audit rows.

The worker sends epoch seconds; the repository converts them to aware UTC
datetimes, so PostgreSQL never parses a caller-supplied value. Imported rows
keep their source values, including states and null timestamps the runtime
guards would refuse today. Source timestamps were converted by the engine
session (`FROM_UNIXTIME`, `NOW()`), so their timezone provenance still needs
verification before import.

## Remaining notebook work

- Wire the repository into the worker, runtime service, package flows and
  Superset previews at cutover; build each worker transaction from the
  runtime's captured principal, role and security-context version.
- Persist queued/running execution intent before wakeup and fence completion
  by runtime generation (currently Redis-only, Wave D).
- Import with timestamp provenance, then rehearse and cut over with every
  other domain.

Validation is recorded in
[the Notebook metadata checkpoint](../../reports/production-refactor/notebook-metadata/README.md).
