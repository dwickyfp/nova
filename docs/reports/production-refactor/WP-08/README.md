# Durable queue storage evidence

This checkpoint implements revision 0005 and the PG job/outbox repositories.
It has no live caller port and cannot activate PG metadata authority. The storage
contract and remaining participant obligations are described in
[PG_DURABLE_JOBS.md](../../../architecture/production-refactor/PG_DURABLE_JOBS.md).

On the existing PG17 TLS fixture and PgBouncer, the combined suite passed
**164 cases with no skips**. Thirty-two cases cover job/outbox behavior; the other
132 preserve migration, bootstrap, pooling, preferences and AI registry checks.
Full-application mypy, changed-file Ruff on 64 Python files, and all 54 agent
scenarios / 189 checks passed. The full unit/coverage suite passed 10,690 tests
with four warnings in 669.13 seconds, with 65% combined coverage.

The first two focused attempts each failed schema setup on a PL/pgSQL CASE
expression. Parenthesizing the expression fixed parsing; no partial migration
committed. The third attempt passed 27 cases on an intermediate tree. Five more
cases and pending-job/outbox consistency were added before the final combined
run. All four sanitized JUnit reports are retained here.

The tests use normal non-owner workload logins with forced RLS. They exercise
concurrent submission and claims, expiry of each armed state, old-token refusal,
blocked resource conflicts, append-only effect history, verified-outcome rollback,
explicit safe retry, unprepared cancellation and outbox lost acknowledgments.
The queue can be polled without Redis notifications. These are controlled storage
tests, not evidence that an external participant cannot commit after a crash.

The fixture removed its test schema and roles after the run. Development
StarRocks, Ranger, Superset, object storage and user data were not reset.

Registry parent commit `130c479b` has passed its CI PG job with 132 cases and zero
skips. Its full CI run is still in progress. These results do not validate the
new uncommitted job code; its own CI run remains pending.

A separate controlled contract probe found that `embed` is listed as a valid AI
function type, but both managed registration paths return `Unknown function type:
embed`. The probe made no external call. It is recorded as R33, not silently
marked passed under the owner's live embedding-test waiver.

Exact source hashes, commands, test totals, attempts and limits are in
[jobs-commands.json](jobs-commands.json). No release or all-feature completion is
claimed by this checkpoint.
