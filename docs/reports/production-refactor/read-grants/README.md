# Read-only tool approval verification

Read-only conversation approval now belongs to the authenticated principal,
login, active role and security-context version. Each new turn reloads its
12-hour Redis record, including expiry and revocation. A mismatched record is
removed only if its value has not changed since the read. Old records without
a login binding cannot restore approval.

The shared tool gate, supervised actions and speculative read execution also
check the approving context. This prevents an overlapping turn from borrowing
another login's in-memory approval. Agent `auto_read_only` policy is stamped
for the current turn and is not persisted as user consent. Changing the policy
to `ask_every_tool` retains only an applicable explicit user approval. Writes
and session changes still require their own approval.

The [initial Redis probe](before.json) reproduced approval transfer to a new
login in both restored and in-memory state. Eleven initial unit regressions
failed. A later overlapping-turn regression exposed the shared runtime holder;
the final fix checks context at dispatch, without changing conversation IDs or
creating another inference loop. The five final shared-loop trajectories also
ran against parent `02195d19` in an isolated process: all four mismatched-context
cases failed, and the matching-context case passed. The running server and
working-tree files were not replaced.

[commands.json](commands.json) retains every test attempt, including corrected
fixtures. Existing approval fixtures now provide valid login/role context and
an explicit approval stamp; their expected tools, denials and role-switch
assertions remain intact. An intermediate ThreadStore change was discarded.
One full unit attempt was interrupted after 7,273 passes when the overlap
defect was reproduced; it is not full-suite acceptance. The next complete run
passed 10,832 cases and failed four cases whose preapproval fixtures had no
login stamp. Correcting those fixtures preserved the original assertions; the
221 affected cases pass. The final full unit/coverage run passes all 10,836 cases without skips.

## Local API and Redis evidence

The [first live attempt](live.json) passed 14 assertions. A subsequent attempt
on the expanded implementation [timed out](live-final.json) while creating a
synthetic conversation, before its approval cases ran. The conversation had
committed despite the missing response. [Reconciliation](reconcile.json)
located its unique fixture prefix within that attempt's creation interval and
deleted it through the ordinary API before another attempt.

The [final attempt](live-repeat.json) passes all 16 assertions: two real logins,
independent approvals, process restoration, cross-login refusal, dispatch
context matching, revocation from another process, real Redis expiry and
compare-and-delete safety. Its synthetic conversation, grant keys and both
logins were removed. The initial reconciliation helper called a nonexistent
client method; its one login was recovered from the exact audit time interval
and logged out, as recorded in [cleanup evidence](clean-helper-login.json).

These are running API/storage checks and controlled helper calls. They invoke
no model or external tool and do not replace live-provider acceptance. The
timed-out attempt remains a failure. The cause of the dependency slowdown is
unverified; neither the later pass nor an operator load waiver erases it.

The source inventory also encountered a lost engine connection before publishing
its first attempt. A later read-only scan completed against the same 157 metadata
tables after fixture cleanup. It preserves all 875 reviewed call sites and 759
feature rows. No release acceptance status was changed.

## Browser and live-provider evidence

The browser regression reproduced a stale approval picker: a pending read-only
call still displayed the previous always-allow mode. It now reflects the server's
request for approval; a pending write or session change does not clear read-only
approval. All 25 hook cases pass. The first full frontend run passed 1,608 cases
and failed one table-hover case: its ordered mock response supplied the pending
second-table result to the first table. The fixture now matches table URLs;
assertions and application behavior are unchanged. All 31 affected browser cases
and the subsequent full 1,609-case frontend suite pass. The earlier failure is
retained in commands.json.

[Six live attempts](live-provider.json) used the same synthetic constant-query
fixture across two configured models, three attempts each. Four pass with exactly
one read-only SQL tool call, matching-session approval, result 42, structured
answer and persisted history. The first Luna attempt failed during conversation
creation before reaching grant/provider checks. The final Sol attempt started
the expected read-only tool but ended with `timeout`; its tool was marked failed
and no valid answer/history was produced. Both remain failures. The five known
threads were deleted and their grants revoked; the owned login was logged out.
[Reconciliation](provider-cleanup.json) found one committed fixture hidden by
the failed creation response, deleted it through the API using the attempt's
unique prefix and confirmed zero remaining fixtures. Its own login was logged out.

The [engine probe](engine-health.json) later completed a constant query in
0.02 seconds. This does not identify the failure's cause or certify reliability.
The local host also ran backend and frontend test suites during these attempts;
this is recorded context, not evidence that resource pressure caused either
failure. No extra provider retries were used to turn these six results green.

## Scope and remaining work

The updated approval labels and `/docs/sql/sql-workspace` guidance were viewed
in the running product. No SQL or Python example changed. The docs browser
suite passes 15 cases, frontend coverage passes 1,609 tests, and all 354 agent
trajectories pass. Full mypy, changed-file Ruff, the 54-scenario/189-check agent
scorecard and the data-access boundary scan pass. Eleven offline benchmark
cases pass; the optional real-engine benchmark skips because its Compose
working-directory guard does not accept the existing dev project. No stack was
created or reset to bypass that guard. Final unit results are in commands.json.

This repairs the existing Redis-backed grant path. PostgreSQL durable consent,
exact pending-call/run binding, cross-process revocation during a running turn,
worker recovery, runtime separation and the global cutover remain open. The
live-embedding and original large-load/soak owner waivers are unchanged. These
results do not establish whole-product release acceptance.
