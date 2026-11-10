# Nova Notebook

Status: implemented on `feat/nova-notebook` (2026-11-10). Design source:
[spec](proposals/nova-notebook-spec.md). Evidence and pinned versions:
[testing/notebook](testing/notebook/README.md).

A notebook is a Jupyter document in the user's Workspace whose Python and SQL
cells run in a governed, isolated runtime: one OpenSandbox container per active
notebook, a persistent Python kernel, and DuckDB connected natively to Nova.

## Shape

```
Workspaces UI (.ipynb tab) ──REST──► notebooks module ──► Redis queue + per-runtime lease
        ▲ SSE (Last-Event-ID)              │                         │
        └── Redis stream nova:nb:ev:<nb> ◄─┴── notebook runtime worker (nova-worker)
                                                    │ OpenSandbox SDK 1.2.0
                                                    ▼
                                  opensandbox-server (compose, only holder of docker.sock)
                                                    │ one container per active runtime
                                                    ▼
          nova-notebook-runtime: Jupyter kernel + execd + nova_runtime (+ egress sidecar)
             db = DuckDB ── ATTACH 'nova' (TYPE mysql, READ_ONLY) ──► MySQL proxy :4406
                                         notebook data token, read-only, as the user's role
```

| Piece | Owner | Notes |
|---|---|---|
| Document (`.ipynb`) | `backend/app/modules/notebooks/document.py`, `documents.py` | nbformat 4.5; stable cell ids; `metadata.nova` holds kind, result name, SQL engine, revision. Outputs are never saved in the file. Stored through `WorkspaceService` (single writer) with revision checks under a Redis lock. |
| Runtime state | `notebooks/runtime/state.py` | Redis only: live runtime hash, notebook → runtime, queues, leases, in-flight executions, cancel flags, events. |
| Worker | `notebooks/runtime/worker.py` | Runs inside `nova-worker`. Drains one FIFO queue per runtime under a lease; the only writer of runtime status and execution rows. Idle stop, lifetime cap, reconciliation of orphans and lost sandboxes. |
| Sandbox seam | `notebooks/runtime/provider.py`, `opensandbox_provider.py` | Only the adapter imports the SDK. It reads execd's raw event stream to keep MIME bundles (the pinned SDK keeps only text). |
| Kernel side | `docker/notebook-runtime/nova_runtime/` | Bootstrap (DuckDB, `nova` attach, display capture), SQL bridge, `{{name}}` references, bounded table previews, variables, package installs. Tested inside the image. |
| Durable rows | `notebooks/repository.py` | `CONFIG_NOTEBOOK_RUNTIMES`, `CONFIG_NOTEBOOK_EXECUTIONS` (written once per execution), `CONFIG_NOTEBOOK_PACKAGES`. Output payloads are objects beside the Workspace (`.notebook-outputs/`). |
| Data tokens | `access_control/data_tokens.py` | Session-bound proxy passwords by purpose (`bi`, `notebook`). |
| UI | `frontend/src/features/workspaces/notebook/` | Notebook tab in Workspaces. |
| Neva | `assistant/tools/notebook.py`, `client_capability.py`, `skill_library/notebook.md` | Reads the open notebook, proposes cells, runs cells and installs packages only with approval. |

## Governance

- **Identity.** A runtime reads Nova data as the signed-in user under their
  active role. The worker mints a *notebook data token* bound to the Nova
  session (principal, role, security-context version) **and** to the runtime:
  it stops working when the runtime stops, the role changes, or the session
  ends. The proxy re-checks the binding before every command.
- **Read-only.** A notebook token only logs in from `NOTEBOOK_SANDBOX_NETWORKS`,
  and every statement on such a connection must be read-only (session settings
  DuckDB sends are allowed; writes are refused with MySQL error 1792 and
  audited). DuckDB also attaches `nova` as `READ_ONLY`.
- **No secret in the sandbox environment.** The Jupyter token is the only
  environment value. The data token travels in a one-time file the kernel reads
  and deletes; DuckDB redacts it in `duckdb_secrets()`. User code can still read
  its own kernel's memory — the token is the user's own, read-only identity,
  which is why enforcement lives in the proxy and Ranger, not in the kernel.
- **Role changes.** Before each run the worker compares the run's session and
  role with the runtime's token and re-attaches `nova` when they differ or the
  token passed half its lifetime.
- **Network.** Sandboxes run with egress deny-by-default (`dns+nft`): only the
  proxy's IP and port are open; the package index is opened for the duration of
  an approved install and closed after.
- **Isolation.** One container per runtime, non-root user, no Docker socket, no
  host binds, capability drop and `no_new_privileges` from the OpenSandbox
  server. On macOS this is Docker `runc` isolation — suitable for development
  and trusted users, not hostile tenants (see Limits).
- **Audit.** Runtime start/stop, every execution (source revision, not source),
  package installs, notebook proxy logins refused and statements refused.

## Execution

1. The UI saves pending edits, then `POST /notebooks/{id}/executions` with the
   saved revision. The server freezes each cell's source from the document
   (never from the request) and queues one job per code cell.
2. The worker holding the runtime's lease runs, per cell: a trusted
   `begin` (start display capture), the cell (Python as is; SQL through
   `run_sql`), then `end` (captured displays and variables). stdout/stderr
   stream live as `cell.output` events.
3. Cancel sets a flag; the worker interrupts the kernel through execd. A cell
   past `NOTEBOOK_CELL_TIMEOUT_SECONDS` is interrupted, and a kernel that does
   not stop within the grace period is restarted (generation + 1).
4. The finished execution is one row plus one output object; the tab gets
   `cell.finished`. A reload reads the latest execution per cell and anything
   still in flight, then resumes the event stream — nothing re-runs.

SQL cells run on one of two engines:

- **DuckDB** (default): in the kernel, with local files, `nova.<db>.<table>`
  (filter pushdown through the attach), and `{{name}}` references to earlier
  results or Python values (scalars bind as parameters, frames register under
  generated names; nothing is spliced as text).
- **Nova**: the whole statement goes to Nova through `mysql_query`, so it runs
  next to the data under Nova's SQL (stages included), read-only. The preview
  keeps the query's order by limiting in place.

A named SQL result is a lazy DuckDB relation in Python. Previews are bounded
(`NOTEBOOK_PREVIEW_ROWS`), never counted with an extra query.

## Proxy compatibility

DuckDB's `mysql` scanner needed these proxy changes:

- `COM_STMT_PREPARE` announces a query's columns, described through the
  governed pipeline as `SELECT * FROM (…) LIMIT 0` (`SHOW`/`DESCRIBE` are run
  to learn theirs). A statement the caller may not read is refused at prepare
  with the engine's own error, as a MySQL server does.
- `START TRANSACTION READ ONLY` is answered by the proxy (writes refused until
  `COMMIT`/`ROLLBACK`).
- On a notebook connection, the scanner's four catalog queries
  (`app/proxy/notebook_metadata.py`) follow Nova's catalog rule instead of raw
  `information_schema`, which Ranger can refuse to a role that may still read
  its own tables: schemas come from `SHOW DATABASES` (hidden databases
  removed); columns from the scanner's own query, or `SHOW FULL COLUMNS` per
  table when that is refused; indexes and size are answered locally.

## Packages and files

- Packages: allowlist (`NOTEBOOK_PACKAGE_ALLOWLIST`), installed with the kernel's
  own interpreter (`pip --user`), committed to the manifest only after a
  successful install and import, restored on every start. Removing a package
  takes effect on the next start.
- Files: uploads go to the notebook's Workspace folder (`data/`, `src/` for
  Python) with type, size and content checks; the folder is copied into
  `/workspace` at start and on upload. Files the code writes to
  `/workspace/outputs` are copied back when the runtime stops.

## Neva and Dataviz

- The notebook registers the `workspace.notebook` surface: the open notebook,
  the selected cell, its last run and the runtime. Neva reads it with
  `notebook_context`, proposes cells through `notebook.propose_cell` (the
  person accepts, accepts and runs, or dismisses a diff), and runs a cell or
  adds a package only after approval each time.
- **Add to Dataviz** is offered for tables of Nova-engine SQL cells whose source
  is unchanged since the run, under the same role. The Dataviz resolver reads
  the notebook record (`thread_id` `notebook:<id>`), so capability check, plan
  and publish are the existing ones. DuckDB and Python results report
  `NO_PROVENANCE`: they have no governed query to keep up to date.

## Configuration

`backend/app/core/config.py`, section "Nova Notebook runtimes". The required
deployment values are `OPENSANDBOX_API_KEY` (same as the server's
`NOVA_OPENSANDBOX_API_KEY`) and `NOTEBOOK_PROXY_ADDRESS` (how a sandbox reaches
the proxy, as an IP). The rest are limits with bounded defaults: 3 runtimes,
2 per user, 1 CPU / 1 GiB each, 20 min idle stop, 4 h lifetime, 5 min per
cell. Without `OPENSANDBOX_API_KEY` notebooks open and edit, and starting a
runtime says runtimes are not set up.

## Limits and deferred work

- macOS Docker (`runc`) is not hostile-tenant isolation; production on Linux
  should validate gVisor or Kata with DuckDB, Arrow and pip.
- Notebooks are per user, like Workspace files: no sharing, viewer or runner
  roles yet.
- Not built: terminal, scratchpad, dependency graph, run descendants, Git,
  HTML export, Plotly rendering (Plotly figures show as their text form; use
  matplotlib), server-side filter/sort over a live result, real-time
  co-editing, an approval workflow for packages outside the allowlist,
  standalone `python main.py` bootstrap.
- External catalogs are reached through Nova-engine SQL; the DuckDB `nova`
  attach lists the default catalog.
