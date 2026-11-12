# Open findings: research, plan and fixes

Source parent: `f886ae60`. The owner asked on 2026-11-12 for research, a plan
and fixes for every finding left open by the Workspace caller-port checkpoint.
Each item records what was found, the decision and how it was verified.

| Finding | Root cause (verified) | Fix | Verification |
| --- | --- | --- | --- |
| Monaco loaded from a public CDN | `@monaco-editor/react` defaults to jsdelivr | Bundle `monaco-editor` 0.55.1 locally: editor core, SQL/Python/Markdown languages and the editor worker, configured once through `monaco-setup.ts` | Workspace editor opened with SQL highlighting and zero CDN requests |
| `GET /backup/recycle-bin` refused | `SHOW CATALOG RECYCLE BIN` and `RECOVER … AS` are another engine's syntax. StarRocks 4.1.4 exposes `information_schema.recyclebin_catalogs`, but answers it only for `root` ("operation can be executed only by root user", FE log) | Explicit 501 refusal with a plain explanation and no engine call; rename-on-recover refused with 400; recover by name unchanged | Live 501 with no engine text; unit tests parse every issued statement with the pinned grammar |
| `GET /pipes` without a database | `SHOW PIPES` needs a current database | Read `information_schema.pipes` on the caller's connection when no database is given | Live 200; unit test parses the statement |
| Notebook start timeout | Sandbox creation shared the 60 s control-call timeout while the server could still be pulling an image | Creation uses the runtime's start budget (default 180 s); orphaned requests stay covered by the existing sweep | Unit tests for both timeouts |
| Credential canaries in the fixture log | A deliberate leak test (`SELECT %s::integer`) ran before commit `d49e2b43` made metadata logging private on 2026-11-11 | No production path. Added a guard that fails on unreviewed `%s::<type>` casts of bound values | Guard test; today's log window is clean |
| Slow endpoints | See below | See below | Timings below, two sequential runs |
| No docs for user-defined functions | The `/functions` page is intentionally outside navigation (NOVA-138) | New page *Create your own functions* (SQL worksheet) | Every example ran through the worksheet's query service as nova_admin (not typed into the editor), then was removed; docs tests pass; page viewed |

### Slow endpoints (local stack, about 220 databases, one request at a time)

| Endpoint | Before | After | Change |
| --- | --- | --- | --- |
| `monitoring/loads/stats` | 65.7 s | 6.3 s, then 0.03 s | Shares the loads list's stale-while-revalidate cache (system-pool data behind the admin gate) |
| `monitoring/alerts` | 8.0 s | 0.9–1.0 s | Six independent probes run concurrently; each keeps its own failure |
| `objects/databases` | 8.1 s | 2.2–3.0 s | `SHOW FULL TABLES` plus one materialized-view listing per database over four caller connections, keeping `?role=` |
| `agents/studio/capabilities` | 8.9 s | 3.3–4.0 s | Agent access checks run two at a time (in order for a delegated caller); still checked on every request |
| `ontology/metrics` | 29.1 s | 16.3–21.4 s | Each ontology kind read once per request; the visible layer reused by the meaning checks |
| `ontology/lint` | 9.5 s | 7.7–9.9 s | Unchanged path; dominated by per-view visibility checks |

The remaining ontology cost is the per-view visibility check (Ranger plus an
`EXPLAIN` on a caller connection) and the full `eval_results` window scans.
Caching visibility across requests conflicts with the deliberate "check every
time" rule and needs an owner decision; it was not changed.

## Defects found and fixed during this QA

- The User-Defined Functions dialog offered only databases that already held a
  function, so with none it could not be submitted. It now lists the databases
  the active role can use.
- The dialog sent arguments as text; the service expects named, typed
  arguments, so every create returned 422. Arguments are parsed in the browser,
  keeping commas inside types such as `DECIMAL(10,2)`.
- Dropping a function issued `DROP FUNCTION IF EXISTS db.name`, which the engine
  rejects; it needs each overload's signature. The service now reads the
  signatures on the caller's connection and drops each one; a missing function
  is 404 and a global function is refused with 400.
- Global functions showed a delete button whose request could not match a route;
  it is replaced by a "Global" label.
- Built-in function rows used an unkeyed fragment (React key warning).
- The engine reports a missing database as 5501, not 1049. The UDF listing and
  the object browser now skip a database dropped while listing.

Live UI checks as nova_admin (ACCOUNTADMIN): the Functions page lists the global
functions; Create UDF returned 201; calling it through Nova SQL returned 42; the
UI delete returned 200 and showed "Function deleted". QA objects and the
temporary docs database were removed and the session signed out.

## Not fixed, with reasons

- **Unexplained page reload** during a notebook run: it did not recur in the
  later notebook QA, and no reload source exists in the notebook code. Without a
  reproduction there is nothing to fix; it stays recorded.
- **Object browser `?role=`**: the faster path keeps the existing behavior.
  Moving it to the session's active role is a product decision.
- **Ontology visibility caching**: needs the owner decision described above.

## Validation

- Backend unit/coverage: 10,938 passed, 0 skipped. Evals 358 passed; scorecard
  54/189; mypy; Ruff on the 22 changed Python files; user-data boundary check.
- Frontend: `pnpm lint` (0 errors; existing warnings only), `pnpm build`, and
  `pnpm test:coverage` 194 files / 1,614 tests passed. An earlier run, made while
  the backend suite was also running, had one Studio chat test time out; that
  file then passed 3 of 3 times alone and in the clean full run. A first run
  also exposed a Monaco test mock missing `loader`, which was fixed.
- Docs: 15 docs tests passed and the new page was viewed at `/docs`.
- The metadata PG suite was not rerun: no PG code changed since the 582-case run.
- Delivered-commit CI has not been checked.
