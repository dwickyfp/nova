# Pipes over stages (W3)

StarRocks `CREATE PIPE` syntax and semantics, run by Nova. The engine never
stores or runs a pipe: an engine pipe would run without the caller's sign-in
and role and would keep storage credentials. Users and the user page:
[Load files continuously with pipes](../../../frontend/src/features/docs/content/sql/12-pipes.md).

## Entrypoints

| Entrypoint | Path |
| --- | --- |
| SQL (Workspace, MySQL proxy, task scripts, HTTP `POST /query/execute`) | Pinned grammar rules `createPipeStatement`, `alterPipeStatement`, `dropPipeStatement`, `showPipeStatement`, `descPipeStatement` → `sql_frontend/pipes.py` (`PipeStatement`, `validate_pipe`, `PipePlanner`) → `NovaActionPlan("pipe", PipePayload)` → `FeatureAdapters.pipe_statement` → `modules/pipes/sql.py` → `modules/pipes/control.py` |
| `information_schema.pipes` / `pipe_files` in a query | `FeatureAdapters.execute_engine` → `modules/pipes/sql.py:intercept` renders the caller's visible rows as a typed derived table and runs the caller's SQL over it on the engine |
| HTTP Pipe API (`/api/v1/pipes`, pre-existing) | Unchanged; it issues the same SQL through `query_service`, so it now drives Nova pipes |
| Database Explorer (`/explorer/databases/{db}`, pipe detail) | `explorer/repository.py:list_pipes/get_pipe_detail` → `modules/pipes/views.py:explorer_pipes` (caller's role) |
| Worker | Scheduler plans the pipe's `pipe.load` task (interval = `POLL_INTERVAL`); `GraphRunWorker._execute_pipe` → `modules/pipes/runner.py` |
| Native fallback | `analysis/validation.py:_native_pipe_execution` refuses every pipe statement that would reach the engine |

No grammar change was needed. Pipe statements are typed AST builders and an
exact-type planner; `QueryService` has no pipe branch.

## Caller identity and execution role

- Statements run as the caller with their single active role; the active role
  becomes the **owner role**. Changes, resume and retry need the owner role;
  suspend and drop are also allowed to `ACCOUNTADMIN`/`user_admin`/`security_admin`.
- `CREATE PIPE` checks, as the creator: Ranger `INSERT`/`ALL` on the target
  table (`effective_access`), stage read through the stage registration's
  Ranger check, a scheduled-task account bound to the role, and an `EXPLAIN`
  of the first batch on the creator's engine connection (`DESC` of the table
  when the folder is empty).
- Runs execute as the role's **scheduled-task account** (the existing
  `config_task_role_bindings` binding, restricted `EXECUTE AS` through the task
  worker) with `SET ROLE <owner role>` confirmed by the engine. The worker never
  uses root or the creator's credentials.

## Metadata owner (PostgreSQL, migration 0056)

| Table | Content | Writers |
| --- | --- | --- |
| `nova_meta.config_pipes` | definition (INSERT text, decoded source with stage id), properties, state, last error, owner role, `task_id`, revision (CAS), `wake_count`, claim token/generation/expiry | web/query (create, property/state CAS, wake, delete); task worker as SERVICE `nova-pipes` (state/last error CAS, claim columns, last poll) |
| `nova_meta.pipe_files` | ledger per (pipe, file name, file version = ETag): size, modified, `UNLOADED/LOADING/FINISHED/ERROR`, batch label, rows, error, staged/start/finish times, retry count | worker inserts and moves states; web/query only `ERROR → UNLOADED` (retry) and delete with the pipe |
| `nova_meta.pipe_batches` | load intent: label, file count, bytes, claim generation, outcome | worker only; a row is refused unless its generation is the pipe's current lease |
| `nova_meta.config_tasks` (`handler='pipe.load'`) | the pipe's schedule, created/changed in the same transaction as the pipe | web/query; the task login may only set a `pipe.load` task to `SUSPENDED` |

Forced RLS: web/query see pipes whose `owner_role` is the active role, or all
pipes for administrators (`pipe_visible`); ledger rows follow their pipe. The
task login sees rows only as the `nova-pipes` SERVICE actor (`pipe_worker`).
Column grants restrict every write; row triggers keep identity and definition
immutable, require `revision+1` for property/state changes, keep lease
generation monotonic and allow only the file and batch transitions listed.
Statement triggers reuse `task_write_guard` (workload login + active epoch).
Inventory: `PG_STORAGE_OWNERSHIP.csv`. These are new PG-native tables with no
`NOVA_SYSTEM` source, so they have no `METADATA_OWNERSHIP.csv` import row.

## External effects and exactly-once

1. Claim: `UPDATE config_pipes SET claim_token, claim_generation+1, claim_expires_at`
   only when unclaimed or expired; every ledger write locks the pipe row under
   the held token and generation (`FOR NO KEY UPDATE`), so a stale run records
   nothing. Renewed every 30 s (lease 180 s) together with the task node heartbeat.
2. Access check (Ranger) as the owner; denial → `SUSPENDED` + reason, schedule
   suspended, nothing loaded.
3. Stage resolved and authorized, then its credentials resolved **for this run
   only** (`storage.storage_config`); list objects matching the glob; new
   (name, ETag) rows staged `UNLOADED`. Without `AUTO_INGEST` the folder is
   listed only on the first run.
4. Settle `LOADING` batches from earlier runs by their label in
   `information_schema.loads` on the owner connection: `FINISHED` → files
   finished; in progress → left; `CANCELLED` → files back to `UNLOADED`;
   not found → submitted again under the **same label**.
5. Batches (`BATCH_FILES`, `BATCH_SIZE`) → intent committed (`pipe_batches`
   row + files `LOADING` + label) → `INSERT INTO t WITH LABEL <label> ...` whose
   source is a `UNION ALL` of one `FILES()` read per file.
   Label = `nova_pipe_<pipe id prefix>_<sha256(pipe id, sorted name+version)>`.
   The engine refuses a label it committed ("Label [...] has already been
   used"); that error is resolved by the label lookup, never counted as a failure.
6. Data failure of a multi-file batch → split into single-file batches; a
   single file failure → `ERROR` with the engine message (credentials redacted,
   storage paths replaced by stage paths). Engine access refusal → batch back to
   `UNLOADED`, pipe `SUSPENDED`. Lost connection/timeout → batch stays `LOADING`
   for the next run (no replay without a lookup).

Credentials exist only in run memory and in the SQL sent to the engine. Plans,
payloads (checked by `NovaActionPlan`), ledger rows, errors, audit and logs
never contain them.

## Failure and recovery

| Failure | Behaviour |
| --- | --- |
| Worker dies mid-batch | Lease expires; next run settles the label (step 4) |
| Two runs for one pipe (schedule + kick) | One claim wins; the other returns "Another run is loading this pipe"; `wake_count` makes the winner do another pass |
| Metadata commit lost after the INSERT | Files stay `LOADING`; next run finds the label `FINISHED` |
| Owner role loses table/stage access | Pipe `SUSPENDED` with reason; `RESUME` re-checks access |
| Stage deleted | Pipe `SUSPENDED` "The stage ... no longer exists" |
| `CREATE OR REPLACE` | New pipe id, empty ledger, new labels: files load again (documented) |
| `DROP PIPE` during a run | Run's fence fails; rows already committed stay |
| A queued file version was replaced or removed | Marked `ERROR` ("no longer in the stage"); the newer version loads as its own file, so old content is never read under a new label |
| Engine CANCELLED a single-file batch (data error) | File `ERROR` with the engine message; a multi-file batch is re-queued and split |
| Scheduling account missing | `CREATE`/`RESUME` refused with the reason |

## Acceptance checks

- Unit: `tests/unit/test_nova_pipes.py` (statement forms, native refusal, validation,
  batching, labels, duplicate labels, lost outcome, split on bad file, revoked
  access, lease, credential absence, replaced file versions, cancelled labels) and
  `tests/unit/test_pipes.py` (the HTTP Pipe API over Nova pipes).
- PG17: `tests/integration/test_metadata_pipes.py` (RLS by role, column grants,
  CAS race, two workers one winner, stale lease refused, ledger transitions,
  drop, task-login suspension only) and `test_metadata_tasks.py` for the
  replaced `task_row_guard`.
- Engine: `tests/integration/test_pipes_live.py` (HTTP API refusal of storage
  URLs/keys, no engine pipe created, statements never reach the engine).
- Live dev E2E: `docs/reports/starrocks-compat/w3-pipe-live.json` (29 steps, with the
  earlier failed attempts and the two defects they found).

## Known limits

- S3-compatible stages only; at most 100,000 matching files per listing.
- Pipes created natively in the engine before this change are not shown or
  managed by Nova statements (Nova never created them; `CREATE PIPE` was refused).
- `ALTER PIPE ... RESUME IF SUSPENDED` is in the StarRocks docs but not in the
  pinned grammar.
- Kick runs (create, resume, retry) are `schedule`-trigger graph runs created
  by the API process; production `query` processes need graph-run insert grants
  the same way `EXECUTE TASK` does.
