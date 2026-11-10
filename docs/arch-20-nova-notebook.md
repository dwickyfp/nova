# Nova Notebook

Status: implemented on `feat/nova-notebook` (2026-11-10). Design source:
[spec](proposals/nova-notebook-spec.md). Evidence and pinned versions:
[testing/notebook](testing/notebook/README.md).

A notebook is a Jupyter document in the user's Workspace whose Python and SQL
cells run in a governed, isolated runtime: one OpenSandbox container per active
notebook, a persistent Python kernel, and DuckDB connected natively to Nova.
`con = novaduck.connect()` is that DuckDB with every catalog the user's role
sees attached (Nova, Postgres, Snowflake), and it writes back to Nova tables,
stages and, through governed write-back, Postgres and Snowflake tables.

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
      con = novaduck ── ATTACH nova, pg_shop, sf_… (TYPE mysql) ──► MySQL proxy :4406
                         one attach per catalog, notebook data token, as the user's role
      con.write / merge_into / put ── request file + stderr marker ──► worker (as the user)
                         Nova: QueryService · stage: stage service · Postgres/Snowflake: write-back
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
| Neva | `assistant/tools/notebook.py`, `client_capability.py`, `skill_library/notebook.md` | Reads the open notebook, proposes cells (with a title), runs cells and installs packages only with approval. |
| Kernel requests | `notebooks/runtime/rpc.py`, `nova_runtime/novaduck.py` | `con.write`, `con.merge_into`, `con.put`: the kernel asks, the worker does it as the user. |
| Write-back | `catalog/writeback.py`, `catalog/sources/writes.py`, `sources/postgres.py`, `sources/snowflake.py` | Governed writes to Postgres and Snowflake catalogs with a separate write credential. |

## Governance

- **Identity.** A runtime reads Nova data as the signed-in user under their
  active role. The worker mints a *notebook data token* bound to the Nova
  session (principal, role, security-context version) **and** to the runtime:
  it stops working when the runtime stops, the role changes, or the session
  ends. The proxy re-checks the binding before every command.
- **Writes run as written.** A notebook token only logs in from
  `NOTEBOOK_SANDBOX_NETWORKS`. Running a cell is the request, as with any SQL
  client: what the role may change is Ranger's decision, the SQL guard and
  `ACCOUNTADMIN` protections apply, and every statement is audited. Neva still
  needs the person's approval to run a cell.
- **Transactions.** DuckDB opens a transaction around writes; StarRocks
  explicit transactions refuse DDL and MERGE, so the proxy answers
  `START TRANSACTION`/`COMMIT`/`ROLLBACK` on a notebook connection itself and
  each statement commits as it runs (a `ROLLBACK` undoes nothing).
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

## Catalogs and writes (novaduck)

- **Attaches.** At runtime start the worker lists the catalogs the role sees
  (`SHOW CATALOGS` as the user) and checks each external one
  (`SHOW DATABASES FROM <catalog>`, 20 s). The kernel attaches every catalog
  that answers, under its Nova name (`nova` for `default_catalog`), each with
  the handshake database `<catalog>.`. A catalog that does not answer is
  listed in `con.catalogs()` with its error and not attached: DuckDB reads
  every attached catalog's schemas when it lists any, so one broken catalog
  would otherwise break them all.
- **Nova tables.** Plain DuckDB SQL writes through the attach
  (`INSERT INTO nova.db.t …`, `CREATE TABLE nova.db.t AS …`). `con.write` and
  `con.merge_into` hand larger data to the worker, which writes it as the user
  through QueryService: batched `INSERT` / `INSERT OVERWRITE`, and Nova's
  `MERGE INTO` (primary-key tables) with a literal source, values rendered by
  the proxy's escaping `sql_literal`.
- **Stages.** `con.put(data, "@stage/path/file.parquet|csv|json")` (or a local
  file) — the worker resolves the stage like SQL does, checks
  `check_stage_access(..., "write")` for the role, uploads through the stage
  service and audits (`notebook_put`).
- **Postgres and Snowflake.** StarRocks 4.1.4 JDBC catalogs refuse every write
  ("fail to begin transaction"), so a DuckDB write through such an attach is
  refused by the proxy with a pointer, and `con.write` / `con.merge_into` go to
  **write-back** (`catalog/writeback.py`):
  1. Ranger first: `role_allows` for the exact `catalog.schema.table` and
     action (INSERT to append; INSERT and UPDATE to merge; DELETE to overwrite
     or delete matches); deny items win.
  2. A separate **write credential** on the connection (security admin,
     Catalog → Connections → Notebook writes, stored encrypted in
     `CATALOG_CONNECTION_WRITE_CREDENTIALS`). The engine's catalog keeps its
     read credential. Without one, writes are refused.
  3. One transaction on the source: rows load into a session table (Postgres
     `COPY`, Snowflake bound inserts), then append, overwrite (delete and
     insert) or `MERGE` (Postgres 15+). Identifiers are quoted by the driver,
     values never spliced; duplicate keys refuse the merge.
  4. Audited (`catalog_writeback`) with row counts; driver errors are masked
     (the write credential's values and credential-shaped text removed).
- **Requests.** The kernel writes the data and a request file under
  `/tmp/nova/rpc/` and prints one stderr marker; the worker reading the cell's
  output strips it, does the request as the runtime's user (session re-checked)
  and writes the result file the kernel waits for. A request carries no
  identity, so a forged one is the user asking for their own rights.
  Limits: `NOTEBOOK_WRITE_MAX_ROWS` (1 M) and `NOTEBOOK_WRITE_MAX_BYTES`
  (200 MB) per request.

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

SQL cells run in the kernel's DuckDB, with local files, every attached
catalog (filter pushdown through each attach) and `{{name}}` references to
earlier results or Python values (scalars bind as parameters, frames register
under generated names; nothing is spliced as text). Nova-only SQL (stage
reads, Nova functions) runs through `con.nova(sql)`. Notebooks saved with the
earlier per-cell engine choice keep running: an `engine: nova` cell still
sends its statement to Nova, and the cell offers to move to DuckDB.

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
- **Add to Dataviz** is offered for a SQL cell that reads only attached Nova
  catalogs (no local files, Python values or `{{refs}}`). The kernel lists the
  tables the query reads (`duckdb.get_table_names`), rewrites the `nova`
  qualifier to `default_catalog` with DuckDB's tokenizer, and the worker checks
  the statement in Nova as the user (`SELECT * FROM (…) LIMIT 0`); only then
  does the table carry `nova_sql`. The Dataviz resolver (`thread_id`
  `notebook:<id>`) uses it when the cell is unchanged since the run and the
  role is the same. Other results report `NO_PROVENANCE`.

## The notebook page

- Cells are frameless: the active cell shows a thin accent on its left, the
  toolbar (run, move up, move down, more) shows on hover and stays on the
  active, running or failed cell. Code sits on a grey Monaco surface with line
  numbers; a static preview with the same gutter shows until a cell is edited.
- Each cell can have a title (`metadata.nova.title`, not part of what runs);
  titles feed the Outline in the side panel. A SQL cell's result name sits
  under it as `→ name`.
- Moves: drag handle, the toolbar, Ctrl/⌘+Shift+↑/↓, or the handle's arrow
  keys.
- Outputs: images, tables (Table/Chart), Markdown. HTML runs in a sandboxed
  frame with an opaque origin and a policy that loads nothing from the
  network; HTML that carries scripts (PyGWalker, plotly) may run its own inline
  scripts in that frame. The kernel shows `pygwalker.walk(df)` as its
  self-contained HTML and sets plotly to embed plotly.js. Libraries that load
  their code from a CDN (altair, bokeh, folium) do not render.

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
- Notebook writes autocommit per statement; a write-back call is one
  transaction on the source, a Nova `con.write` of many batches is not.
- Write-back needs the backend to reach the source's host. A host-run dev
  backend cannot reach compose-only hosts (the `catalog-test-postgres`
  fixture); the containerised backend can.
- Altair, bokeh and folium need CDN scripts, which output frames do not load.
- Not built: terminal, scratchpad, dependency graph, run descendants, Git,
  HTML export, Plotly rendering (Plotly figures show as their text form; use
  matplotlib), server-side filter/sort over a live result, real-time
  co-editing, an approval workflow for packages outside the allowlist,
  standalone `python main.py` bootstrap.
- External catalogs are reached through Nova-engine SQL; the DuckDB `nova`
  attach lists the default catalog.
