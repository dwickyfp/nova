# Workspace guide

Inherit the [root](../../../../AGENTS.md) and [backend](../../../AGENTS.md)
contracts. For PG work also read the
[metadata guide](../../../metadata_migrations/AGENTS.md) and
[Workspace port record](../../../../docs/architecture/production-refactor/PG_WORKSPACES.md).
Notebook content changes also follow the [notebook guide](../notebooks/AGENTS.md).

## Domain boundaries

- Keep workspace ownership/shares, file metadata and object storage under this
  domain. Notebook documents use its file lifecycle; do not add a second file
  writer in a router, agent tool or notebook runtime.
- Personal namespaces identify the person. Shared namespaces use `~ws/<id>`;
  they are not usernames. Resolve the caller's access through the owning space
  service before reading or changing an entry. A hidden workspace remains hidden.
- Shared file access uses assigned roles, with edit/view permissions; it does
  not activate those roles for data queries. SQL and Notebook data execution
  still use the caller's single active role and Ranger. Workspace ownership
  does not grant another person's data or notebook runtime.
- Only the owner manages a workspace's name and shares. Sharing a file does
  not transfer ownership. Audit the acting person, not the shared namespace.
  Revalidate identity and handle revoked/stale sessions at the established
  authorization boundary; a prepared PG scope does not fix session revocation.

## File/version transactions

- Preserve entry, workspace and version IDs, owners, file formats and existing
  API behavior through migration. Use the source ownership/constraint review;
  current runtime and prepared PG repositories must remain distinguishable.
  Do not wire a prepared store into production before approved cutover.
- Reserve versions and use revision CAS rather than assuming one writer or
  relying on an unlocked `MAX(version)+1`. Preserve duplicate historical rows
  and report ambiguous version reads/imports explicitly; never discard IDs.
- Publish metadata with immutable object receipts and the correct expected
  revision. Handle cancellation, lost commit responses, orphan objects and
  incomplete publication through the owning recovery path. Do not delete an
  object after an uncertain commit until reconciliation establishes its owner.
- Keep subtree rename/delete and share replacement atomic in metadata. Treat
  names and paths literally: `%` and `_` must not widen a subtree selection.
  Check collisions, permissions and version retention under concurrent writes.
- A metadata transaction does not make object-store effects atomic. Test the
  actual service lifecycle and recovery in addition to repository fixtures.
  User-facing file access never exposes storage credentials or physical endpoints.

## Validation

Run backend gates and relevant real-PG/engine checks from the owning migration
state. Cover personal/shared access, assigned-but-inactive roles, view/edit/owner
boundaries, another user's files, revision conflicts, version history, subtree
operations and partial storage failures. Verify SQL files and Notebook callers
through their normal paths when they change. Follow frontend/docs validation for
visible revisions, permissions, errors or file actions. Record prepared storage,
prepared caller ports, active runtime and completed cutover as separate evidence.
