# Workspace metadata port

Revision 0013 prepares `CONFIG_WORKSPACES` and `CONFIG_WORKSPACE_SHARES` in
`nova_meta`. This is storage preparation for the all-domain cutover. Current
HTTP and Notebook callers still use `SpaceRepository` and `WorkspaceService`
against `NOVA_SYSTEM`. No application starts a second metadata write path.

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

## Remaining workspace and notebook work

- Port file entries and immutable file-version metadata, with reserved versions,
  revision CAS, immutable object receipts and publication recovery.
- Preserve the two observed duplicate historical version groups. Verify object
  contents and emit an explicit conflict manifest; never discard source IDs to
  make a uniqueness constraint pass.
- Port the owning API/service transactions and expose required revision conflicts
  through their established response and user-documentation paths.
- Port Notebook package, execution, runtime generation and claim metadata. The
  existing per-person package identity defect remains open.
- Rehearse the full import, verify the migration manifest and all writers, then
  activate PG authority once. Prepared repository tests are not migration or
  whole-feature acceptance.

Validation is recorded in
[the Workspace checkpoint](../../reports/production-refactor/workspace-sharing/README.md).
