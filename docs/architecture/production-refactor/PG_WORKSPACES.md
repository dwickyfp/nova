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

## Remaining workspace and notebook work

- Port `WorkspaceService` and the Notebook callers to these repositories inside
  one metadata transaction per operation, with content-addressed or reserved
  object keys so a lost writer cannot overwrite the live object, then expose
  revision conflicts through the established 409 response and user docs.
- Implement the version-reservation recovery worker: verify each `reserved`
  row against object storage, then publish or abandon it.
- Produce the redacted duplicate-version conflict manifest and stop import until
  its reconciliation policy is applied.
- Port Notebook package, execution, runtime generation and claim metadata. The
  existing per-person package identity defect remains open.
- Session-role revocation (stale cached assignments) remains open.
- Rehearse the full import, verify the migration manifest and all writers, then
  activate PG authority once. Prepared repository tests are not migration or
  whole-feature acceptance.

Validation is recorded in
[the Workspace sharing checkpoint](../../reports/production-refactor/workspace-sharing/README.md)
and [the Workspace files checkpoint](../../reports/production-refactor/workspace-files/README.md).
