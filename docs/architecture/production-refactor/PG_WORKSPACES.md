# Workspace metadata port

Revision 0013 prepares `CONFIG_WORKSPACES` and `CONFIG_WORKSPACE_SHARES` in
`nova_meta`. Revision 0014 prepares `CONFIG_WORKSPACE_ENTRIES` and
`CONFIG_WORKSPACE_FILE_VERSIONS`. This is storage preparation for the
all-domain cutover. Current HTTP and Notebook callers still use
`SpaceRepository`, `WorkspaceRepository` and `WorkspaceService` against
`NOVA_SYSTEM`. No application starts a second metadata write path.

## Ownership and permission contract

`WorkspaceSharingRepository.bind` accepts the authenticated server session
inside an existing `MetadataDatabase.transaction`. It verifies the transaction's
principal, active role, login ID, principal type and security-context version.
Assigned roles are scoped to that transaction/savepoint. Every repository
operation verifies the bound context again; nesting or reusing a closed scope
is refused. Exceptions and cancellation roll back the scope and clear its roles.

The existing sharing behavior is preserved: a share matches any assigned role,
with `edit` taking precedence over `view`. The active role is never changed by
file sharing. SQL and Notebook data access retain their separate caller-role
and Ranger checks. Only the workspace owner manages its name or shares; an
administrator does not gain ownership of another user's files.

Forced RLS exposes these stores to the web, query, agent and notebook workload
identities. Only web can mutate ownership/share metadata. Other workload logins
have no table grants. A fixed owner function resolves visibility without
recursive RLS; it verifies the runtime login and actor context before looking
up the owner or shares. It does not return another workspace's data to a caller
without an applicable share.

This contract trusts authenticated role assignments supplied by the server.
It does **not** solve the existing stale-session revocation finding in
[the workspace review](WAVE_A_REVIEW.md). Role/session invalidation and its
concurrency tests remain necessary before runtime cutover.

## Transactions and history

Creation assigns the existing UUID-hex identifier format. Names use the existing
workspace validation. Rename, share replacement and deletion require a positive
expected workspace revision. One concurrent writer advances it; a stale writer
receives `MetadataConflict` (409). Share replacement holds the parent row lock
and replaces the complete set in one savepoint. A failed statement cannot leave
the workspace revision advanced with only part of its share set installed.

Share removal retains a revisioned tombstone; regrant advances that same row.
Workspace deletion retires the shares and workspace together. Runtime roles
cannot hard-delete or truncate either table. The owning file-deletion workflow
must drain before calling this metadata deletion; no cross-store atomicity is
claimed. Database triggers append success audit events in the same transaction,
including the principal, active role, object key and revision. Names and file
contents are not copied into these audit events.

The migrator can import only under maintenance and the exclusive deployment
barrier. Source IDs, owners, grants and nullable source timestamps are retained.
Runtime-only ID/name constraints do not rewrite historical imports. Existing
timestamp provenance still requires verification, and no source rows have been
imported or cut over by these fixture tests.

## File entries and versions (revision 0014)

`WorkspaceFileRepository` uses the same actor-bound scope as the sharing
repository. A namespace is either the principal's own name or `~ws/<id>`.
`workspace_namespace_permission` returns `owner` for the principal's personal
namespace and delegates shared namespaces to `workspace_permission`, so an
assigned-role share grants file access without changing the active data role.
Reads need any permission; inserts and updates need `owner` or `edit`. A
view-only caller who tries to write receives a permission refusal rather than a
revision conflict. Administrators do not see another person's files. Only the
web workload can write; query, agent and notebook logins read; all others have
no grants.

Entries keep the source column set and add `metadata_revision`. Database
guards refuse empty, `.`/`..` and slash-bearing names, unnormalized folders,
unknown entry types, folders with object keys and files without them. A partial
unique index allows one live entry per `(namespace, folder, name)`, so two
entries cannot share one object key. File saves publish a new live receipt
against the revision that was read; a concurrent writer receives a conflict.
Folder moves and deletions lock and change the folder and every descendant in
one savepoint. Descendants match by literal path prefix, not `LIKE`, so `_` and
`%` in names cannot widen the subtree. Moves return the `(old, new)` object keys
and deletions return every retired entry and version object key. The caller
reconciles object storage after commit; no cross-store atomicity is claimed.

A version is reserved before its object is written. Reservation locks the file
row and takes the next number above every retained row, including imported
ones; a unique index on runtime rows backs this up. Six concurrent reservations
receive 1–6. Publication records size, ETag and SHA-256 in one transition from
`reserved`; abandonment closes a reservation whose write did not happen. Both
are single-use. Published receipts are immutable and only `reserved` rows are
listed for recovery. Deleting a file retires its versions with tombstones;
runtime roles cannot hard-delete or truncate either table.

Imported version rows keep their opaque IDs and are marked `imported`.
Duplicate historical `(entry, version)` occurrences are retained; reading such
an ambiguous number returns a conflict instead of choosing one. Import still
needs object verification and an explicit conflict manifest before cutover.
Success audit records only the row ID and revision: names, paths, object keys
and file contents are not copied into audit metadata.

## Prepared caller port

`WorkspaceMetadataService` is the prepared replacement for `WorkspaceService`
file commands. It keeps the method names and arguments that the Workspace router
and Notebook callers use today (`create_file`, `create_file_bytes`,
`create_folder`, `get_file`, `read_entry_bytes`, `update_file`, `rename_entry`,
`delete_entry`, `list_entries`, version reads), so wiring it in at cutover does
not change those callers. It is not wired in yet; nothing in the running
application imports it.

Each command uses one actor-bound metadata transaction. Object effects go
through the existing Workspace storage owner:

- Every save writes a new object under a fresh `.objects/<entry>/<random>` key,
  then updates the entry with a revision check. The previous live object becomes
  the new version in that same transaction, with its size, ETag and SHA-256, so
  history needs no copy. A source row without an ETag records `sha256:<digest>`.
- A concurrent writer from the same revision gets 409 and its new object is
  removed. A permission refusal (403), metadata failure, version-receipt
  failure or cancellation also removes it and leaves metadata unchanged.
- An identical save writes nothing. An object-store outage changes no metadata.
- Renames and moves change metadata only; object keys are opaque and stay.
- Deletion retires metadata first, then drains entry and version objects. A
  failed object deletion leaves an unreferenced object and a logged count;
  metadata is never restored for it.
- An unknown commit outcome leaves the new object in place for recovery.
- Opening an imported duplicate version number returns 409.

The new object layout leaves legacy path-derived and `.versions` objects in
place: the first save of an imported file keeps its legacy object as history.

## Remaining workspace and notebook work

- At cutover, wire `WorkspaceMetadataService` into the router and Notebook
  callers, pass the read revision from the editor so saves report 409 to users,
  and update the Workspace user docs with the conflict message. Notebook
  output objects (`internal_object_key` and its put/read) carry no metadata and
  stay with `WorkspaceService`. One Notebook site reads
  `workspace_repository.get_entry` directly and must move to the service.
- Version reservations made by this service publish in the same transaction, so
  it leaves no `reserved` rows. A recovery worker is still needed for unknown
  commit outcomes and for any other reserver.
- Produce the redacted duplicate-version conflict manifest and stop import until
  its reconciliation policy is applied.
- Port Notebook package, execution, runtime generation and claim metadata
  (`CONFIG_NOTEBOOK_*`); these tables have no PG store yet. The existing
  per-person package identity defect remains open.
- Workspace UI preferences use the separately prepared preference store.
- Session-role revocation (stale cached assignments) remains open.
- Rehearse the full import, verify the migration manifest and all writers, then
  activate PG authority once. Prepared repository tests are not migration or
  whole-feature acceptance.

Validation is recorded in
[the Workspace sharing checkpoint](../../reports/production-refactor/workspace-sharing/README.md)
and [the Workspace files checkpoint](../../reports/production-refactor/workspace-files/README.md).
