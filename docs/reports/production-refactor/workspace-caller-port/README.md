# Workspace caller port, worker readiness and UI QA checkpoint

Source parent: `4d33e5d4`. This checkpoint adds the prepared Workspace caller
port, fixes two defects found during UI QA and records the remaining findings.
Runtime Workspace and Notebook callers still use `NOVA_SYSTEM`; nothing in the
running application imports the PostgreSQL port.

## What changed

- `WorkspaceMetadataService` keeps the `WorkspaceService` method names. Each
  save writes a fresh object, then updates metadata with a revision check and
  turns the previous object into the version in the same transaction. Losing,
  refused or failed writes remove their object. See
  [the Workspace contract](../../../architecture/production-refactor/PG_WORKSPACES.md).
- Worker startup waits up to 180 seconds for an unavailable engine or Redis.
  Before, one slow StarRocks planner response (about 13 s) made the `task` role
  exit; the fail-fast supervisor then stopped all seven roles, and Notebook
  runtimes could not start until a file change restarted them.
- The User-Defined Functions tab failed for "All databases" with an engine
  error. The pinned engine has no all-database listing and uses `Signature`,
  `Return Type` and `Function Type` columns. The listing now reads global
  functions plus each database (220 databases in 0.3 s locally) and the error
  response no longer carries raw engine text.

## Validation

All local results are recorded in [the command record](commands.json).

- Real PG: 582 tests pass with zero skips, including 11 caller-port cases with
  a fault-injecting object store. Removing the object cleanup made 6 of them
  fail (mutation check), so the orphan assertions are effective.
- Unit/coverage passes 10,916 tests with zero skips (final run after the launcher fix); 358 evals, the 54/189 scorecard, mypy, Ruff on the eight changed
  Python paths and the user-data boundary check pass.

## UI QA on the running stack (nova_admin, ACCOUNTADMIN)

Passed in the browser: sign-in session, Workspace tree, new SQL file, run
`SELECT 1 …` (one row), save, version history showing the earlier content,
rename to an existing name refused (409, dialog stays open), valid rename,
delete with confirmation, and the fixed User-Defined Functions tab (8 global
functions). The QA file and QA notebook were deleted and the session signed out.

API smoke over 113 parameterless GET endpoints: 91 returned 2xx. The 422s were
missing required parameters; `catalog/search` needs a term; `intelligence/incidents`
reports the News prerequisite. Ten endpoints exceeded 40 s under six parallel
calls and all returned 200 sequentially; `monitoring/loads/stats` took 65.7 s
and `ontology/metrics` 29.1 s.

## Follow-up: Notebook QA and launcher restart (same day)

With the owner's approval the pinned OpenSandbox egress image was pulled by
digest (Docker reported it already present, so the sandbox server had finished
its own pull after the earlier 60-second timeout). Notebook QA then passed in
the browser: runtime start, a DuckDB SQL cell (`42`), a Python cell reading its
result (`42`), a SQL cell reading a Nova table through the `nova` catalog
(`SELECT * FROM nova.notebook_examples.orders` → `1, 100`) and the documented
`con.nova(...)` call (`[(1, 100)]`). The temporary example database and the QA
notebook were removed afterwards and the session signed out.

Two more defects were found and fixed:

- The notebook docs told readers to switch a SQL cell's engine to Nova, but no
  such control exists; the notebook tests deliberately keep new SQL cells on
  DuckDB. The page now shows the verified `nova.<database>.<table>` form.
- A file-watching restart killed the worker launcher after 5 seconds. Its roles
  run in their own sessions, so they kept running and held their metrics ports;
  the replacements failed to bind and the launcher stopped every role again.
  Roles now stop when their launcher exits, and a held metrics port is retried
  within the readiness deadline. A live SIGKILL of the launcher left no role
  running, and the next start brought all seven up while one waited for its port.

## Open findings

- `GET /backup/recycle-bin` is refused by the Nova SQL frontend
  (`SHOW CATALOG RECYCLE BIN`). No active UI calls it; the grammar change is open.
- `GET /pipes` without a database returns a raw engine message; the active UI
  uses the per-database Explorer route instead.
- The SQL editor loads Monaco from a public CDN. An offline production
  deployment would show a blank editor.
- One full page reload happened while a notebook run started; tabs were restored.
  The cause was not identified and it did not recur.
- The User-Defined Functions tab has no user-docs page (existing debt).
- Slow endpoints listed above need profiling before capacity claims.
- A notebook runtime start can exceed the sandbox client's 60-second create
  timeout when the egress image must be pulled first.
