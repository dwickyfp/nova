# Workspace sharing storage checkpoint

Source parent: `5eb29e94`. This checkpoint adds revision 0013 and the owning
Workspace PostgreSQL repository. Runtime callers, entries/file versions and
Notebook metadata are not cut over. The feature QA matrix is unchanged.

The new store preserves assigned-role file sharing without changing the active
data role. It binds principal, login, role and security-context version, rejects
changed or nested scopes, scopes workload grants, and applies share replacement,
revision advancement, tombstones and success audit in one transaction.

The existing dedicated PG17 TLS fixture was reused in the existing `docker`
project. No additional engine or database stack was started. Fixture teardown
removes only its marked synthetic schema/roles and refuses pre-existing state.
All 531 PG tests pass without skips, including 42 Workspace cases. They cover
concurrent replacements, cross-user/editor/admin refusals, all workload grants,
changed context, scope cancellation, rollback, maintenance and preserved imports.
The database log scan found no credential canaries or ciphertext markers.

The first agent-evaluation run recorded 357 passes and one failure: a negative
substring assertion searched the SSE envelope, where a random run UUID happened
to contain `999`. The fixture now deliberately uses a UUID containing that
sequence, verifies the envelope identity, and asserts the exact corrected answer
text `West revenue was 120.`. No production agent behavior or success condition
was relaxed. The affected trajectory file and all 358 evals pass after the fix;
the earlier failure remains in [the command record](commands.json).

The final local unit/coverage run passes 10,909 tests with zero skips. The agent
scorecard passes 54 scenarios and 189 checks; mypy, Ruff on 132 changed Python
paths and the user-data boundary check pass. Frontend and runtime-container
behavior are unchanged by this storage checkpoint.

A read-only source/object probe also checked the two previously observed
historical version collision groups. Four rows reference two available objects;
lengths and ETags match their current bytes. No rows, objects or runtime state
were modified. This is a bounded current-object observation, not a frozen
migration snapshot or proof of overwritten historical content. See
[the sanitized profile](historical-object-profile.json).

[The command record](commands.json) contains counts, source/artifact hashes and
the remaining gates. Local results do not substitute for delivered-commit CI,
patched-FE acceptance, migration rehearsals or whole-feature/live-AI QA.
The preceding `3f6b3789` passed all eight CI jobs, with 489 PG passes and
217 engine passes / 24 skips retained:
[verified CI evidence](../ci-3f6b3789.json).

The [implementation contract](../../../architecture/production-refactor/PG_WORKSPACES.md)
and [delivery backlog](../../../architecture/production-refactor/DELIVERY_STATUS_AND_QA_BACKLOG.md)
record the unfinished migration, scaling and QA work. Session-role revocation,
file version/object receipt handling and caller transactions remain open.
