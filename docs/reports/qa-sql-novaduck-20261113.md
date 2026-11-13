# QA: SQL capability sweep and novaduck as native DuckDB — 2026-11-13

Branch `feat/qa-test`. Same local dev topology as
[the earlier QA report](qa-workspace-notebook-agent-20261113.md). Signed in as
`nova_admin` (`ACCOUNTADMIN`). Notebook runtime image `nova-notebook-runtime:dev`
with DuckDB 2.0.0.

## Goal

1. Every SQL capability: the examples in `/docs` sql/01–10 and tasks/00–09
   (tasks, task graphs, schedules, streams, stream-triggered tasks), every
   statement family the SQL frontend accepts, the destructive-confirmation and
   refusal contracts, and parity through the MySQL protocol.
2. novaduck: `novaduck.connect()` is the only Nova-specific call; after it,
   plain DuckDB 2.0 SQL and Python API, reading and changing Nova tables. No
   Nova-invented syntax or functions in what users are taught.

Decision taken with the user: the older helpers (`con.write`, `con.merge_into`,
`con.put`, `con.nova`, `con.catalogs`) stay working for existing notebooks and
for what DuckDB cannot do natively (Postgres/Snowflake write-back, saving into
a stage), but are no longer documented for users.

## Method

- SQL: each docs example run in order through the worksheet API, plus a
  positive and a negative case per statement family, the same statements
  through the MySQL protocol, and the confirmation contract with
  `confirm_destructive=false`. Every failure reproduced twice and traced to
  source before classification.
- novaduck: a notebook of single-purpose Python cells, each using only
  `novaduck.connect()` and DuckDB, with asserted results, run in the real
  runtime (kernel, governed proxy, engine). A second notebook runs the new
  docs page's examples verbatim.

## SQL sweep results

Run / pass per family before fixes (worksheet path unless noted): sql/01 14/14,
sql/02 22/20, sql/03 15/15, sql/04 16/16, sql/05 14/14, sql/06 13/12, sql/07 5/5,
sql/08 24/23, sql/09 MySQL protocol 26/20, sql/10 9/9, tasks/00+02 12/12,
tasks/03 19/19, tasks/04 11/9, tasks/05 11/11, tasks/06 9/9, tasks/07 streams
27/27, tasks/08 7/6, confirmation contract 29/27, other families 25/21.

### Fixed (each re-run live after the fix)

| # | Defect | Cause | Fix |
| --- | --- | --- | --- |
| S1 | `CREATE TASK` with a `BEGIN … END` body, a `$$` block or a stream body failed with "violates a record constraint" (tasks/04, 06, 08 examples) | `0040_internal_task_handlers.sql` handler allowlist omitted `sql_script` | Migration `0046_sql_script_task_handler.sql` (applied to dev, schema 45 → 46); tasks/04 and tasks/08 examples now create and run |
| S2 | A trailing `-- comment` or `/* */` became its own statement and stopped the whole script | `sql_guard.split_sql_statements` kept comment-only fragments | Comment-only fragments are dropped |
| S3 | `ALTER TABLE … TRUNCATE PARTITION`, `SWAP WITH`, `REPLACE PARTITION` removed or replaced rows without the confirmation dialog | `semantics.py` treated only `Drop…` clauses as deleting | They now ask for confirmation |
| S4 | Worksheet `BEGIN; INSERT; ROLLBACK` reported success but the row stayed; session `SET` silently lost | Each worksheet statement runs on its own pooled connection | The worksheet refuses transactions and session-only `SET` with a plain message (MySQL clients keep real transactions) |
| S5 | Unknown stage showed an internal placeholder name | `stages.py` used the rewritten reference | `Stage '<name>' not found in <database>` with an error code |
| S6 | `SHOW AVAILABLE ROLES` listed every role, including roles `USE ROLE` then refused | Answered like `SHOW ROLES` | Lists the session's assignable roles |
| S7 | `CREATE TASK` note named the engine and internal statements | `adapters.py` message | "Task saved. It has not run yet: …" |
| S8 | MySQL clients: `MERGE INTO` failed ("Table does not exist") and every later statement on the connection failed with "Packet sequence number wrong" | Planning first runs synchronously and abandons the coroutine at its first suspension (`run_without_awaiting`); MERGE's binder had already written to the client's shared engine socket, so its reply stayed unread | The shared-socket path yields before writing (`repository.execute_as_user`); regression test fails without the fix |
| S9 | MySQL clients: `SHOW/CREATE/DESCRIBE TASK` failed with "Metadata operation is not authorized" | The proxy bound no metadata actor, and its least-privilege metadata login has no task grants by design | The proxy binds the connection's user as actor, and refuses task statements up front: "Tasks are managed in Nova, from the SQL worksheet or the Tasks page"; documented in sql/09 |
| D | Docs: sql/08 `GRANT ACCOUNTADMIN` row, sql/02 missing `events` rows, sql/01/04 confirmation list, data/02 stage messages, admin settings sentence | | Corrected |

### Open

- `SUBMIT TASK` (the engine's native background task) is accepted while
  `CREATE PIPE` is refused for not keeping the caller's identity — an owner
  decision.
- `CREATE ML_MODEL` with a `TARGET` that is not selected fails only as
  "Training failed" and leaves a failed model record.
- A script's preflight ignores a `USE db` earlier in the same script.
- EXPLAIN ANALYZE rows contain terminal colour codes; `LIST @stage` shows a
  1970 date for folders; some errors have no `error_code`; `SHOW TASKS IN` an
  unknown database returns empty; `ALTER USER … REQUIRE PASSWORD CHANGE` note
  names the engine.
- During the sweep a task run (`QA_SQL_20261113.default.py_summary`) was left
  `abandoned`; `DROP TASK` refuses a task with a run in progress and there is
  no way to resolve an abandoned run. The record remains on the dev stack.
- The sweep briefly created a user (`qa_never`) in breach of the test rules;
  it was dropped at once and `SHOW USERS` confirms it is gone.

## novaduck results

Final run: 44 of 48 cases pass using only `novaduck.connect()` and DuckDB 2.0;
the docs page's 13 examples pass verbatim (three runs: 12/13, 12/13, 13/13,
the misses being the intermittent stream issue below).

Works natively: `duckdb_databases()`, `information_schema`, `SHOW TABLES FROM`,
`DESCRIBE`, `SUMMARIZE`, filters pushed to Nova, `FROM`-first and
`GROUP BY ALL`/`ORDER BY ALL`, joins across Nova tables and with local
tables/DataFrames, `QUALIFY`, `PIVOT`/`UNPIVOT`, `EXCLUDE`/`REPLACE`/`COLUMNS`,
lists/structs/`unnest`, date, regexp and JSON functions, recursive CTEs,
macros, `?` and `$name` parameters, the relational API, `.df()`,
`.arrow().read_all()`, querying a pandas frame by name, `COPY … TO` parquet and
csv, `ATTACH ':memory:'`, `ASOF JOIN`, `mysql_query`/`mysql_execute` on
`nova`; writes `INSERT` (values, select, from pandas), upsert by `INSERT` on a
primary-key table, `UPDATE`, `DELETE … WHERE`, `CREATE TABLE`, `CREATE TABLE …
AS`, `ALTER TABLE … ADD COLUMN`, `DROP TABLE`, `CREATE`/`DROP SCHEMA`, and —
after the fix below — `TRUNCATE` and `DELETE … WHERE true` on any table.

Shown in the notebook UI with typed cells: connect, upsert, `FROM`-first read,
`TRUNCATE`, `PIVOT` to a DataFrame.

### Fixed

| # | Defect | Cause | Fix |
| --- | --- | --- | --- |
| N1 | DuckDB `TRUNCATE` and `DELETE … WHERE true` refused ("Delete must specify where clause") | DuckDB's MySQL connector sends them as a `DELETE` without a usable condition | On notebook connections the proxy runs an unconditional `DELETE` as `TRUNCATE TABLE` (same table, governed pipeline) |
| N2 | A stalled kernel stream left the runtime BUSY forever: no cell timeout, Stop queued behind it, the runtime kept the user's quota (seen live, two runtimes stuck) | The worker's trusted kernel calls (`begin`, `end`, `bootstrap`, `rotate`) had no deadline | Deadline on every trusted call; a stall becomes a reported failure. After `USE nova.<db>` the next cell now runs |
| N3 | Users and Neva were taught Nova-only helpers | Docs and skill | Docs page, Neva skill and tool text teach only `connect()` + DuckDB; helpers kept for old notebooks, Postgres/Snowflake write-back and stage files |

### DuckDB connector limits (documented, not changeable without Nova syntax)

- `MERGE INTO` on Nova tables: "UPDATE syntax not supported" — use upsert by
  `INSERT` on a primary-key table.
- `CREATE OR REPLACE TABLE nova.…`: the connector sends an unqualified
  `DROP TABLE` ("No database selected").
- `USE` / `SET search_path` to a Nova catalog: the catalog attach has no
  default database.
- `BEGIN … ROLLBACK` does not undo Nova writes: the proxy commits each
  statement (engine transactions refuse DDL).
- DuckDB 2.0 itself: relations have no `fetchone()`; an unread streaming result
  (`.arrow()` reader) blocks the next query on the shared connection.

### Open

- Intermittent: a cell's output stream from the sandbox ends after its first
  event ("The connection ended before Nova received the cell result"). The cell
  did run. 2 of 39 docs-example cells across three runs, on different cells;
  4 occurrences in the worker log today. Lies between the worker and the
  sandbox's streaming endpoint.
- Deleting a notebook does not stop its runtime; the runtime is stopped by the
  idle sweep.
- The dev metadata PostgreSQL restarted three times today (a server process
  exited with code 2; it recovered in about 90 seconds each time). Every
  metadata call fails during recovery.

## Worksheet re-test in a Workspace `.sql` file (UI, typed)

Run in `Untitled-9.sql` through the browser as `nova_admin`, on database
`stream_lab`. Results checked with a query that returns PASS/FAIL per
expected value, not only "statement succeeded".

Data script (one confirmed script run, 10 statements: CREATE TABLE ×3, INSERT,
INSERT … SELECT … GROUP BY, UPDATE, DELETE, INSERT, MERGE INTO (update +
insert), CREATE VIEW with DATE_TRUNC, CTAS with CTE + RANK window). Affected
rows 5/3/1/1/1/4/4 as expected. Data check **11/11 PASS**: row count 5, total
730.50, UPDATE applied, MERGE Jakarta 2/150.50, Bandung 1/200.00, Medan inserted
1/80.00, 4 city rows, view October 350.50 and November 380.00, window rank 1 =
cici, CTAS 4 rows.

Governance seen on the way: `stream_lab_role` was refused CREATE TABLE on
`stream_lab` (correct; the role's grants were not widened); the error showed
once.

Tasks (all created in the worksheet):

| Task | Kind | Owner | Run | Result |
| --- | --- | --- | --- | --- |
| `qa_t_sql` | single INSERT | ACCOUNTADMIN | EXECUTE TASK | success; log row 5 / 730.50 PASS |
| `qa_t_block` | `BEGIN … END` script | ACCOUNTADMIN | EXECUTE TASK | success; 2 steps PASS |
| `qa_t_dollar` | `$$ … $$` script | ACCOUNTADMIN | EXECUTE TASK | success; 2 steps PASS |
| `qa_t_py` | Python, writes with plain DuckDB SQL | ACCOUNTADMIN | EXECUTE TASK | success; 4 city rows, Jakarta 150.50 PASS |
| `qa_g_root` → `qa_g_child` | graph (AFTER) | ACCOUNTADMIN | EXECUTE TASK on root | success; both steps PASS |
| `qa_t_sched` | SCHEDULE EVERY 1 MINUTE | stream_lab_role | schedule | 2/2 success, rows written PASS |
| `qa_t_stream` | WHEN SYSTEM$STREAM_HAS_DATA | stream_lab_role | stream trigger | 1/1 success; order 7 captured as INSERT, stream consumed PASS |

The Tasks page shows the same tallies (2/2, 1/1). Scheduled and stream runs
need a scheduling account for the owner role; `stream_lab_role` has one on this
stack, ACCOUNTADMIN does not (and was not given one).

### Found and fixed in this re-test

| # | Defect | Cause | Fix |
| --- | --- | --- | --- |
| T1 | Every `EXECUTE TASK` typed in a Workspace file failed: "Could not queue task run. Retry later." | The worksheet is served by the query workload; its metadata login could create and drop tasks and enqueue jobs, but had no grant and no RLS policy to record a manual graph run (`0016`: web, scheduler, task only). Earlier sweeps called the web process directly and missed it (false negative). | Migration `0047_query_manual_task_runs.sql`: column INSERT grant and the insert policy for the query workload; state changes stay with scheduler/task. Applied to dev (46 → 47), integration test added |
| T2 | tasks/04 taught `con.write` in Python tasks | Docs | Example now writes with `con.sql("INSERT INTO … SELECT * FROM df")` into a primary-key table; run live: output identical to the docs, a second run keeps 3 rows (no duplicates), the missing-table error text updated to DuckDB's |

## Checks on the delivered source

| Check | Result |
| --- | --- |
| Backend unit suite | 11,240 passed |
| mypy `app` | no issues |
| Eval scorecard (`tests/eval`) | 358 passed |
| Ruff on changed files | passed |
| Metadata migration / schema unit tests | 259 passed |
| Notebook runtime image kernel tests | 39 passed |
| Frontend lint / build | 0 errors / passed |
| Docs content tests | 15 passed |
| PG17 integration tests for 0046/0047 | collected, not run (needs a disposable PG17; CI) |
| Frontend `pnpm test:coverage` | 1651 passed, 1 failed: `notebook.test.tsx` Markdown-editing test timed out under full-suite load (17.5 s); the file passes 36/36 twice on its own |
