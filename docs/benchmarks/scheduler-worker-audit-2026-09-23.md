# Scheduler and Worker Production Audit — 2026-09-23

## Scope and verdict

Reviewed `CREATE TASK` lowering, schedule planning, Redis Streams delivery,
worker execution, native StarRocks observation, and reconciliation. The path is
**not yet production ready** for unattended warehouse writes. A live one-minute
task completed on a stock StarRocks test FE. Ranger authorization on the main
deployment and crash-safe native execution remain release gates.

## Fixed in this audit

| Area | Change | Verification |
| --- | --- | --- |
| `QUEUE` overlap | The oldest pending run may start while later pending runs wait. Previously every pending sibling blocked every other sibling. | Pending sibling regression tests. |
| Worker memory and throughput | Each process admits at most four graph runs, and each graph executes at most four ready nodes at once by default. Stream reads use available capacity. Reconciliation shares the same execution slots. | Slow-run and wide-graph tests. |
| StarRocks system pool | Native polling borrows a system connection for one read and releases it before sleeping or writing a heartbeat. Previously up to 16 concurrent nodes could hold a pool of 10 connections for hours and block their own metadata writes. | Pool-release regression test. |
| Same-second native runs | The pre-submit watermark records the native query IDs at the latest timestamp. Polling skips those IDs and can recognize a new run created in the same second. | Tied-timestamp regression test. |
| Uncertain crash outcome | An `abandoned` node is no longer eligible for automatic resubmission. The graph remains active for inspection, so a `QUEUE` successor does not run over an uncertain warehouse write. | Restart regression test. |
| Redis recovery | Consumer names are unique per process; `XAUTOCLAIM` advances its cursor across pages and wraps after the end. Stale deliveries are considered before new ones. | Cursor and concurrency tests. |
| Scheduler lease | A long tick renews its Redis lease; loss of the lease cancels the tick. Durable runs without a publish remain recoverable. | Long-tick and lock-loss tests. |
| `CREATE TASK` graph identity | New child and join edges inherit the predecessor's graph ID. A scheduled root with two children and a join now plans one graph run. | Lowering-to-scheduler regression test. |
| State safety | A pending graph claim can no longer rewrite a run that settled between the read and the claim. | Race regression test. |
| Unattended owner identity | The standalone worker requires a dedicated account configured in its environment. Each operation opens a fresh connection, executes as the stored task owner, verifies `CURRENT_USER()`, and activates the stored role before owner SQL. The worker never scans expiring login sessions. | Impersonation order, identity mismatch, unsafe owner, and configuration tests. |
| Stored interval syntax | `CREATE TASK ... SCHEDULE EVERY(INTERVAL 1 MINUTE)` stores `INTERVAL 1 MINUTE`; the scheduler now parses that exact form. | Lowering and parser regression test; live scheduled run. |
| Standalone graph scope | A graph with no edges now loads its root task by graph ID. Previously it reached a failed graph run with no node. | Repository scope regression test; live scheduled run. |
| Worker role verification SQL | `CURRENT_ROLE()` and `CURRENT_USER()` use safe aliases accepted by StarRocks. The previous aliases were rejected by the FE parser before owner SQL could run. | Impersonation unit test and live executor preflight. |
| Repeated native submissions | The native task name derives from the durable node-run ID, so every scheduled attempt has a distinct one-shot template and polling cannot select a same-named task in another graph. A completed template is dropped under the owner identity; task-run history remains. | Live two-cycle cron and interval tests; one-cycle test checked zero surviving templates and a persisted query ID. |
| Scheduler idempotency read load | Existing due-run IDs are checked in batches of 500 instead of one database query per historical occurrence on every tick. | Batch-path regression test. |

## Open findings

### Deployment gate — provision the dedicated worker account

The previous worker used `SessionCredentialProvider`; a daily schedule lost its
owner's password when the default one-hour login session expired. The worker
entrypoint now requires `WORKER_IMPERSONATION_USER` and
`WORKER_IMPERSONATION_PASSWORD` from its environment/secret manager and refuses
`root` or `nova_admin`. Create a separate StarRocks user with `IMPERSONATE` on
each permitted `'owner'@'%'` account individually. Do not grant `IMPERSONATE ON
ALL USERS` or ordinary warehouse data privileges to the worker account. Nova
uses StarRocks
[`EXECUTE AS ... WITH NO REVERT`](https://docs.starrocks.io/docs/sql-reference/sql-statements/account-management/EXECUTE_AS/)
on a fresh connection per operation and checks `CURRENT_USER()` before running
the owner's SQL. The owner account must use the `%` host form and a simple
identifier in the current implementation. Confirm the grants, role activation,
and Ranger behavior on the deployed StarRocks version before enabling schedules.
When Ranger is enabled, `WORKER_IMPERSONATION_ROLE` is required so the worker
sets an explicit active role before `EXECUTE AS`. The stock test FE accepted a
scoped grant and executed the task. The main Ranger-enabled FE returned error
5204 for `EXECUTE AS` even after the StarRocks grant and role were present.
The FE warning log names the underlying denial as missing `IMPERSONATE` on the
owner user; earlier attempts also hit the Ranger active-role requirement.
The corresponding Ranger policy or authorization integration still needs to be
resolved and tested end to end. The service account password is never stored
in `NOVA_SYSTEM`.

### Critical — uncertain native outcome still requires operator resolution

`DelegateExecutor.execute` submits the native task before polling it. The
native `QUERY_ID` reaches `CONFIG_TASK_RUNS` only when the node is finalized.
If the worker dies after submit, reconciliation eventually changes its durable
row from `running` to `abandoned`. Previously the worker resubmitted that node
while the first StarRocks run might still be executing. It now holds the graph
in `running` with an `abandoned` node and requires inspection instead of
silently replaying `INSERT`/`INSERT OVERWRITE`. This protects against that
automatic duplicate, but the run does not complete on its own. The attempt
now has a deterministic native name; recovery still needs a protocol to attach
to that name and an idempotency contract for data-writing bodies before a
crashed worker can recover automatically.

### High — existing graph rows and cross-graph joins need migration

The old lowering stored each child's edges under the child's own graph ID.
Already persisted fan-out graphs can still plan duplicate root executions.
This audit fixes newly created single-root DAGs. When new predecessors belong
to different stored graph IDs, lowering now fails clearly rather than merging
rows without a transaction. Existing edges need a checked migration and an
active-run policy before the new canonical identity can be applied safely.

### High — StarRocks metadata writes need a real concurrency probe

The code relies on `INSERT ... WHERE NOT EXISTS` and conditional `UPDATE`
affected-row counts for run and node claims. StarRocks Primary Key tables use
upsert semantics, and its SQL transaction documentation says concurrent writes
have no write-conflict check. The current unit fakes model atomic claims; they
do not prove the same behavior under two live FE sessions. Run an integration
race against the deployed StarRocks version before treating duplicate native
submission as excluded. See the StarRocks documentation on
[Primary Key tables](https://docs.starrocks.io/docs/table_design/table_types/primary_key_table/)
and [SQL transactions](https://docs.starrocks.io/docs/loading/SQL_transaction/).

### Medium — recovery and planning still scale with control-plane history

`reconcile_once` drives recovered runs serially. One long native run can delay
later lost deliveries for the full poll timeout even though normal stream
consumption continues. Its first-page scan is capped at 1000 pending runs.
The scheduler also loads all task and edge rows every tick and materializes up
to five due occurrences per root. These are metadata rather than warehouse
result rows, but a load test with the expected task count and outage backlog is
needed before sizing production instances. The obsolete session credential
provider still scans Redis if called directly, but the standalone worker no
longer uses it.

## Verification

- Live stock StarRocks/Redis smoke: created `CREATE TASK ... SCHEDULE
  EVERY(INTERVAL 1 MINUTE)` through `QueryService`, then ran the actual scheduler
  tick, Redis consumer, and worker. The task fired after one minute; at **64.1
  seconds** the sink contained **1 row**, the graph and node were both
  `success`, and the node had a native StarRocks query ID. The isolated fixture
  was removed after verification.
- Task and `CREATE TASK` interception unit suite after the live fixes:
  **527 passed**. Ruff and `git diff --check` passed on the changed
  orchestration, worker, and smoke test files.
- A two-cycle cron test completed with two successful graph/node runs, two
  sink rows, and distinct native attempts. A two-cycle 30-second interval
  test did the same, with two persisted native query IDs. A later one-cycle
  test found zero remaining native task templates after completion.
- The live smoke was on the stock test FE; it does not certify the
  Ranger-enabled main FE, concurrent claims, or crash recovery.

## Release gate

Resolve automatic recovery of uncertain native outcomes, authorize and test
worker impersonation on the Ranger-enabled main FE, migrate existing split
graph definitions, drain in-flight runs created with the old native naming
scheme before deploying the new reconciler, and
run live tests for concurrent claims, worker termination after native submit,
Redis loss, long tasks, and a representative backlog. Until then, do not
advertise scheduled warehouse writes as exactly once or unattended-safe.
