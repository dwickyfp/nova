# CREATE TASK schedule and body matrix — 2026-09-23

## Scope

This audit follows `CREATE TASK` parsing and metadata lowering through Nova's
scheduler, Redis transport, worker, and the stock StarRocks test FE. It does not
certify the Ranger-enabled main FE, which still rejects worker `EXECUTE AS`
with error 5204. Tests use isolated databases, users, roles, and streams; the
fixtures are removed after each successful run and failed fixtures are cleaned
by exact suffix.

## Schedule results

| Surface | Result | Evidence |
| --- | --- | --- |
| `EVERY(INTERVAL 30 SECOND)` | Live pass | Fired at 35.5 s; a separate two-cycle test produced two successful graph/node runs, two query IDs, and two sink rows. FE latency made that test take 129.4 s. |
| `EVERY(INTERVAL 1 MINUTE)` | Live pass | Fired at 64.1 s in the prior smoke test. |
| `EVERY(INTERVAL 1 HOUR)` / `1 DAY` | Deterministic scheduler pass | `CREATE TASK` lowering and `plan_tick` produce exact UTC due instants. Waiting hours or days was not part of the live test. |
| `'* * * * * UTC'` | Live pass | One minute-grid fire produced a successful `INSERT` and native query ID. A two-cycle run produced two successful graph/node runs and two sink rows in 103.7 s. |
| `'USING CRON * * * * * UTC'` | Live pass | One minute-grid fire produced a successful CTAS and native query ID. |
| `'*/1 * * * * UTC'` | Live pass | One minute-grid fire produced a successful `INSERT OVERWRITE` and native query ID. |
| 5-field steps, ranges, lists, weekdays, and embedded zones | Parser and scheduler tests pass | The exact lowering-to-plan matrix covers minute, hourly, daily, and weekday cases. |
| DST spring gap / autumn repeated hour | Deterministic tests pass | A nonexistent wall time is skipped; the repeated wall time yields two distinct UTC fires. A 150-case minute-scan comparison across UTC, New York, and Berlin had zero mismatches. |
| Zero interval / invalid zone / 4- or 6-field cron | Rejected at creation | A task cannot be stored with a schedule the tick would silently skip. |
| `START('...') EVERY(...)` | Rejected at creation | The previous parser discarded `START`, silently changing requested timing. A future implementation needs persisted start semantics. |

The scheduler caps catch-up at five newest occurrences per tick. A long outage
can discard older fires; this remains a production behavior to choose and
document, not an exactly-once guarantee.

The two-cycle test initially exposed a repeat-run failure: StarRocks rejected
the second `SUBMIT TASK` because the first one had already used the logical
task name. Each durable node attempt now has a deterministic unique native
name. The worker and reconciler use that name to observe the correct run; the
worker drops the completed one-shot template. A live follow-up confirmed one
successful run with its query ID still present and zero remaining native
templates. The scheduler batches due-run existence checks to avoid one read
per repeated historical occurrence on every tick.

## Body results and limits

| Body | Result |
| --- | --- |
| `INSERT INTO ... SELECT` | Live task success; sink row checked. |
| `INSERT OVERWRITE ... SELECT` | Live task success; sink row checked. |
| `CREATE TABLE ... AS SELECT` | Live task success after granting the owner `CREATE TABLE` on the database; generated table row checked. |
| `CACHE SELECT` on a local OLAP table | Native task failed clearly: StarRocks reports that cache select is not supported on local OLAP tables. No external catalog exists on the test FE for a successful cache test. |
| `INSERT ... SELECT AI_COMPLETE(...)`, `AI_SENTIMENT(...)`, `ML_PREDICT(...)` | Task syntax accepted. The test FE has no corresponding global UDFs, so provider/model execution is not certified. |
| `UPDATE`, `DELETE`, bare `SELECT`, `CREATE ML_MODEL` | Rejected by the `CREATE TASK` grammar before metadata is stored. Nova's native worker uses StarRocks `SUBMIT TASK`, which supports only INSERT, CTAS, and CACHE SELECT. |
| `MERGE INTO` | Rejected by Nova's task grammar; the stock StarRocks 4.1 FE also rejects `MERGE` at the first token. Nova cannot promise MERGE semantics without a separate implementation. |
| `@stage` in a task body | Rejected at creation and by the worker for pre-existing rows. Native `SUBMIT TASK` would store the rewritten `FILES(...)` SQL with injected storage credentials in its task definition. |
| Multiple SQL statements after `CREATE TASK` | Rejected. A trailing semicolon remains valid. Previously the parser silently used the first statement and ignored the rest. |

The [StarRocks `SUBMIT TASK` reference](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/ETL/SUBMIT_TASK/)
lists INSERT, CTAS, and CACHE SELECT as its supported ETL statements. The
[StarRocks `UPDATE` reference](https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/UPDATE/)
and [DELETE reference](https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/DELETE/)
describe standalone DML, not native task bodies. Adding those to Nova tasks
requires a separate execution mode with bounded results, owner authorization,
heartbeat, cancellation, retry, and crash recovery; accepting the syntax alone
would cause delayed runtime failures.

## Remaining release gates

The current result is not 100% coverage of Nova or StarRocks SQL. The main
Ranger FE needs an authorized impersonation path. Direct DML, Nova ML training,
AI provider calls, and external-catalog CACHE SELECT need dedicated end-to-end
fixtures. Crash recovery and concurrent claim behavior remain open in the
scheduler/worker audit. These are explicit unsupported or unverified paths,
not successful production guarantees.

The focused task and SQL interception suite passed 527 tests; Ruff and
`git diff --check` passed for the touched scheduler/worker code.
