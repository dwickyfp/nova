# QA: Workspaces, Notebooks and Agents against /docs — 2026-11-13

Branch `feat/qa-test`, cut from `origin/main` at `577760fc` (tree identical to
`feat/production-control-plane` at `198331d4`). Local dev topology: host
`dev.sh` (backend `--reload` on 8000, Vite on 5173), compose project `docker`
for infrastructure, notebook runtime through the local OpenSandbox. Signed in
through the browser as `nova_admin`, active role `ACCOUNTADMIN`. Live LLM:
the configured default provider.

## Method

1. Focused backend units for workspace, notebook, agent, assistant and studio
   (84 files) before any change.
2. Every step on the docs pages below was performed in the browser with real
   clicks and typed text, and the observed result compared with the page:
   `sql/01-sql-workspace`, `notebooks/01..03`, `ai-ml/05-agents`,
   `ai-ml/06-skills-and-tools`, `studio/01,02,04,08`.
3. Each suspected defect was reproduced, its network response or DOM read, and
   its cause found in source before it was called a bug (false-positive check).
   Boundary cases were run on both sides (false-negative check).
4. Fixes were verified again in the browser, then all suites were rerun.

## Defects found and fixed

| ID | Area | Observed | Cause | Fix |
| --- | --- | --- | --- | --- |
| F1 | Workspaces | A failed statement printed its error twice. | The API repeats the error in `warnings`; the panel rendered both. | `queryErrorMessages` shows each message once. |
| F3 | Workspaces | With about ten open tabs the whole workspace shifted 40 px left: the file panel was clipped ("orkspaces") and later tabs were unreachable. | The tab strip did not scroll; focusing the active tab scrolled the `overflow-hidden` workspace container. | The strip scrolls horizontally on its own, keeps **+** outside, brings the active tab into view, and its menu is the shared portaled dropdown so scrolling cannot clip it. |
| F4 | Workspaces | `SELECT … INTO OUTFILE` showed only "Request failed". | The refusal is HTTP 403 with the statement results as the body; the client looked only for `detail`. | `ApiError` keeps the parsed body; the worksheet shows the refused statement's own message: "INTO OUTFILE exports query results to a file and is not read-only." |
| F5 | Workspaces | Renaming an open file from the file panel left its tab under the old name, although the dialog says "Open tabs keep working under the new name". | The explorer reported deletes to the page but not renames. | `onRenamed` updates the open tab's title. |
| F6 | Notebooks | After **Restart kernel** (and after editing a cell) old outputs looked current. The restart dialog promises "Earlier outputs are marked out of date". Seen on a real notebook: a cell edited to `'Joko2'` above saved output still printing `Joko`. | Regression in `06b41420`: the stale state was still computed but no longer passed to the cell. | The cell shows **Out of date, run again to update** in place of its run time and dims the output. Verified: fresh run 0 marked, after restart 2 of 2 marked. |
| F8 | Agents | A just-created agent showed "Updated … 6:45 AM" at 1:45 PM (UTC+7); its Version history on the same page showed 1:46 PM. | Agent Studio views serialized naive UTC metadata times without a zone. | Agent Studio view timestamps are serialized as UTC (`…Z`), like the assistant and Studio views. Header now shows 1:46 PM. |
| F9 | Agents | The trash icon deleted the agent immediately, with no confirmation, although deletion is irreversible and removes the agent's memories. | No confirmation step. | `ConfirmDialog` "Delete <agent>?" with **Delete agent** / **Cancel**. Verified with Cancel on a real agent (kept). |
| F2 | Home | Recent work labelled notebooks and CSV files "SQL query". | Every workspace entry was typed `query`. | Entries are typed **Notebook**, **Data file** or **SQL query** by name. |

## Documentation corrected

| ID | Page | Change |
| --- | --- | --- |
| D1 | `sql/01-sql-workspace` | **Format SQL** is **Cmd+Shift+F** on a Mac; Ctrl+Shift+F does nothing there (verified both). |
| D2 | `notebooks/01-create-and-run` | Describes the restored **Out of date, run again to update** marker. |
| D3 | `ai-ml/05-agents` | Delete now asks for confirmation. |

## Open, not fixed here

| ID | Area | Observed | Why left open |
| --- | --- | --- | --- |
| F7 | Home / query history | After `novaduck.connect()` the user's Recent work and query history fill with internal probes: `SHOW CATALOGS`, `SHOW DATABASES FROM <catalog>`, `SELECT * FROM (...) AS nova_prepare_describe LIMIT 0`. | Query history has no source field to tell runtime/catalog probes from user statements; a correct fix tags the source where history is written (backend, history owner), not a client-side text filter. |
| O1 | Workspaces | History lists a destructive statement that only asked for confirmation (then cancelled) as **ERR**. | Minor wording; it was not run. Kept as is. |
| O2 | Agents | Enter in the Create agent dialog does not submit. | Minor. |
| O3 | Notebooks | At 375 px the notebook toolbar's runtime label ("Stopped") overlaps the download icon. A tab activated at desktop width is not scrolled back into view after the window narrows. | Existing layout issues outside this change; noted for the notebook toolbar owner. |
| E1 | Environment | Catalogs `sf_sales` and `sr_remote` fail to attach in notebooks. | Connection configuration of this dev stack, not code. |

## Verified as documented (no defect)

Workspaces: file panel tabs and **Personal Workspace**; **SQL file** and the
**More things to create** menu (SQL file, Notebook, Folder, Shared workspace);
Run / Ctrl+Enter / Cmd+Enter on the statement under the cursor; **Run all**
(Shift+Cmd+Enter) giving **Result 1..6**, stopping at the first failure with a
warning icon and not running statement 7; `USE <db>` switching the tab's
database; 500-row cap note shown at 501 rows and **not** at exactly 500;
`EXPLAIN` labelled **Explain**; `Invalid SQL: 1:7 …` format; **Run destructive
query?** — **Cancel** ran nothing, **Run query** deleted 1 row; `USE ROLE`
mixed with another statement in one selected run refused with the documented
message (Run all sends statements separately, as the page notes); History
**Current file / All files** newest first; **Version history** list; file
context menu **Rename / Move to… / Delete**.

Notebooks: default cells, **Start runtime** → Ready, **Run all**;
`duckdb.sql('SELECT 41 + 1')` → `[(42,)]`; novaduck `connect()`, `con.nova`
→ `[('ACCOUNTADMIN',)]`, `catalogs()` with alias `nova`; `.fetchone()` refused
with guidance; `write` append → `[(1, 100)]`, `merge_into` →
`[(1, 120), (2, 200)]`, `overwrite` → `[(3, 300)]`; **Result name** `regions`
→ `rows 2`; `nova.<db>.<table>` read in DuckDB; matplotlib image; traceback
display; restart clears variables and resets the counter; Markdown double-click
edit and Ctrl+Enter render; **Saved**; **Stop runtime**.

Agents and Studio: AI & ML menu labels; Agent Studio list and **Create agent**;
detail tabs and Configuration sections; **Save** → "Draft saved…"; **Version
history** compare (Saved draft vs Active configuration, 1 changed field) →
**Publish selected version** → "Agent version published"; usage by UTC day
including today; **Skill Registry**, **Tools Registry**; Studio opens in a new
tab; rail items; one live question to Stock Analyst answered in about 45 s with
steps, SQL + Copy, Source data (1 table), follow-ups, token count and the five
answer buttons. The monthly sell-out figures (311,587 / 287,618 / 310,371)
match an independent engine query exactly. Account menu and **Agent memory**
match the page.

False positives ruled out during the run: an apparent Cmd+Enter failure (a
stale screenshot; Cmd+Enter works), repeated History titles in Studio (distinct
threads from an earlier benchmark), unlabelled Create agent fields (labels are
associated; the tool showed placeholders), garbled multi-line Python (editor
auto-indent while typing, not the product).

## Checks on the delivered source

| Check | Result |
| --- | --- |
| Backend focused units before changes | 1546 passed |
| Backend full unit suite | 11166 passed |
| Agent trajectory suite (`tests/eval`) | 358 passed |
| mypy `app` | no issues, 733 files |
| Ruff on changed backend files | passed |
| Frontend `pnpm lint` | 0 errors (31 existing warnings, none new) |
| Frontend `pnpm build` | passed |
| Frontend focused browser tests | tab strip, notebook, query messages, explorer, docs: passed |
| Frontend `pnpm test:coverage` | 196 files, 1646 tests passed |

Not run: real-engine integration suites (no DB access, SQL, auth or storage
code changed) and a second role's view of these pages.

Test data created during the run (database `QA_TEST_20261113`, a SQL file, a
notebook and a temporary agent) was removed through the UI. One Studio
conversation remains in nova_admin's history.
