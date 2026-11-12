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
- Unit/coverage passes 10,913 tests with zero skips; 358 evals, the 54/189 scorecard, mypy, Ruff on the eight changed
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

## Open findings

- Notebook runtime cannot start: the pinned OpenSandbox egress image is missing
  from local Docker, and the sandbox create request times out after 60 s. The
  notebook reports "The notebook runtime service did not answer in time" and
  marks queued cells as not run. Restoring the image needs a registry pull.
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
