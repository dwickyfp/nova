# PostgreSQL login-context verification

Metadata transactions now bind `nova.session_id` to the supplied actor with
transaction-local settings. Contexts without a login explicitly clear it. Invalid
login values are rejected before acquiring a connection and are not echoed in
errors. This propagates Nova's existing authentication context; it does not add
another credential validator or require a login for service metadata operations.

Stage commands compare login identity alongside principal, active role, security
version and principal type before and after policy I/O. A different login cannot
reuse a policy decision obtained for the transaction's actor.

## Reproduced gaps

Before implementation, six invalid-login unit cases failed their expected early
validation, and three real-PG cases failed because transactions had no login
setting. A fourth real-PG regression showed that a Stage service with a different
login was accepted against the existing actor-bound transaction. Those attempts
are retained in the sanitized XML files and commands.json.

The corrected focused suites pass 24 unit and 111 real-PG cases. Direct-pool tests
reuse one physical backend across two logins of the same principal/role, a service
actor and an actor without a login. They inspect exact values inside transactions
and absence outside them. PgBouncer cases cover concurrent contexts, cancellation,
rollback and backend reuse. Stage cases reject mismatched supplied actors and a
session change during policy evaluation without exposing a Stage scope.

The complete backend unit/coverage suite passes 10,842 cases, the complete PG
suite passes 489 cases, and all 354 agent trajectories pass, with no skips.
Full mypy, Ruff on 109 changed Python files, the 54-scenario/189-check scorecard
and the user-data boundary scan also pass. The PG log scan finds no credential,
encrypted-envelope or session canary, and cleanup leaves zero metadata schemas
or roles. Exact source hashes and results are recorded in commands.json. Fixture setup refuses pre-existing
metadata, creates only owned schemas/roles and verifies its ownership marker
before cleanup. Tests reuse the existing PG17 container and a local pinned
PgBouncer process; no additional Docker stack or development-data reset is used.

## Limits

This is a prepared metadata-boundary change. Existing migration files and their
checksums remain unchanged; no new schema revision or metadata epoch is activated.
The source scan confirms the same 875 reviewed StarRocks call sites. Its earlier
live schema observation retains its original timestamps and is not represented
as a fresh scan. No feature acceptance row changes.

Runtime metadata still uses StarRocks. Durable consent, exact run/step identity,
audit-session projection, job execution binding, domain ports, production Compose,
rehearsals, cutover and whole-feature acceptance remain open. Existing live-AI
failures and the owner's embedding/load waivers are unchanged. Frontend source
is unchanged from the validated c71ba4b1 checkpoint.
