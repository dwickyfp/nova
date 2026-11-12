# Tool proposal identity verification

Provider tool-call IDs were also Nova's public approval and prefetch keys. A
provider reusing an ID could collide with another turn, allow a previous decision
to resolve a later pending action, or make the first call consume the second
call's prefetched result. Three controlled trajectories reproduce these failures.

Each provider proposal now receives a server-generated UUID. One proposal keeps
that identity through prefetch, public events, tool dispatch and artifact
provenance. A new proposal gets a new identity even if the provider reuses its
wire ID or response object. Provider messages retain the original wire ID for
both the assistant tool call and its tool-role result. Existing stored object
IDs and historical events are unchanged.

The focused regression run passes 605 cases, including all 358 agent trajectories.
It verifies old-decision replay, simultaneous turns, prefetch executing each SQL
exactly once, MCP ownership, immediate approval, guards and artifact provenance.
An additional trajectory preserves one execution for a repeated response object
while assigning each proposal its own identity. Seven unit cases cover generated
identities, synthetic calls and malformed IDs.
The earlier 15 fixture failures are retained: they assumed public IDs equalled
provider IDs. Updated assertions bind to the actual event's ID and separately
check the original provider reply ID, preserving the authorization, dispatch,
provenance and result checks.

Four running-API flows pass with a controlled local provider deliberately reusing
one wire ID. Requests using the wire ID or an earlier approval ID receive 404
without consuming the current approval; current Allow/Deny decisions still work.
Subsequent reads retain their existing grant. All 16 provider request checks
preserve wire protocol pairing. Only constant SQL runs; all owned conversations,
grants, provider/model and login are removed. This is controlled provider evidence,
not real-model acceptance. Separately, both configured models pass three live
SQL-tool turns each (six total), with explicit per-action approval, UUID public
IDs, the known SQL result, required output schema and persisted history. All six
fixtures and the owned login are cleaned. These are new runs against changed
code; earlier live-AI failures remain recorded and unresolved.

The initial benchmark selection passed 11 offline cases and skipped its engine
case because that fixture requires a disposable test-stack working-directory
label. No duplicate stack or development reseed was created. A separate offline
run retains measurements; the engine skip is not a passing result. This change
does not alter the physical query execution path or establish a capacity claim.

The updated SQL worksheet guidance was viewed in Chrome and its 15 docs browser
cases pass. The source inventory keeps all 875 reviewed metadata call sites and
retains the earlier live schema observation timestamp. PG code, migration
checksums, authority and frontend application code/dependencies are unchanged.
Exact hashes and full-suite results are recorded in commands.json.

Final local checks pass 10,859 unit/coverage cases without skips, full mypy,
Ruff on 115 changed Python files, the user-data boundary scan and the
54-scenario/189-check scorecard. The complete trajectory suite has 358 passes.
The offline benchmark has 11 passes; its separate engine skip remains recorded.

A separate two-login API probe reproduces the still-open pending-consent gap:
the second login can approve or deny the first login's action (two FAIL, two
follow-up read PASS). Both logins and all synthetic resources are cleaned. That
baseline is retained for the next fix; proposal identity does not close it.

Pending approval login/role/version binding, durable PG consent, restart recovery
and mid-turn remote revocation remain open. The proposal UUID is an identity,
not permission to execute a tool. Whole-feature release acceptance is incomplete.
