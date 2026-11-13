# Workspace files storage checkpoint

Source parent: `22b6e708`. This checkpoint adds revision 0014 and
`WorkspaceFileRepository` for `CONFIG_WORKSPACE_ENTRIES` and
`CONFIG_WORKSPACE_FILE_VERSIONS`. Runtime callers are not cut over; the feature
QA matrix is unchanged.

The store scopes personal and `~ws/<id>` namespaces through the existing
assigned-role share rules, keeps one live path per namespace, applies revision
CAS to saves, moves and deletes whole subtrees in one transaction, reserves
version numbers above every retained row and records single-use object
receipts. Imported duplicate version rows are kept; reading an ambiguous number
returns a conflict.

The existing PG17 TLS fixture in the `docker` project was reused with the
pinned PgBouncer binary. No other engine or database stack was started. All
571 PG tests pass with zero skips, including 40 new Workspace file cases:
cross-user, administrator and view-only refusals, every workload grant,
concurrent saves (one winner), six concurrent reservations (1–6), receipt
immutability, wildcard-safe subtree moves, rollback of a failed move, subtree
tombstones, maintenance refusal, database-level name/type checks and
maintenance-only import with retained duplicates.

The first focused run failed: a trigger used `chr(0)`, which PostgreSQL rejects.
The clause was removed. The first full-PG invocation collected no tests because
of shell word splitting; it was rerun. Both are retained in
[the command record](commands.json).

Local unit/coverage passes 10,909 tests with zero skips; 358 evals, the
54/189 scorecard, mypy, Ruff on the three changed Python paths and the
user-data boundary check pass. Today's database log window has no credential
canaries. The full container log still holds 26 matches from 2026-11-11 earlier
sessions; that finding stays open until its source test is identified.

No runtime path uses the new store, so no browser QA applies. Delivered-commit
CI, caller ports, recovery, import and cutover remain open. See
[the implementation contract](../../../architecture/production-refactor/PG_WORKSPACES.md).
