# Custom SQL Tools Audit — 2026-09-23

> Validation of Agent Studio custom SQL tools as Nova's parameterized procedure layer.

---

## Scope and fixes

- Custom tools now render typed values safely, support optional parameters, validate identifiers, and substitute placeholders only in SQL code rather than strings or comments.
- Consent classification uses the rendered statement, so a parameter cannot turn an approved read into a stage export without a new write approval. Approval previews withhold argument values.
- Procedure execution uses the authenticated caller, active role, query dialect pipeline, stage access checks, and query audit. Blocked preflight attempts also write an audit event without SQL or argument values.
- Stage export is allowed only from an approved custom tool; the ordinary SQL guard still blocks the egress forms it previously blocked. Protected `ACCOUNTADMIN` operations remain blocked.
- SELECT results expose a bounded, redacted table to the agent and chart path. Partial failure reports the failed statement index and does not claim success.
- The database connection applies `SET ROLE` before selecting the database, allowing scoped roles that have no default role. Agent tool creation rejects duplicate names; renames update agent selections. The editor resets unsaved changes on cancel.

## Verification

| Check | Result |
| --- | --- |
| Focused backend tests: custom tools, guard, query role, results, routes, trajectories, benchmark | 134 passed |
| Frontend custom-tool configuration browser tests | 4 passed |
| Frontend TypeScript and ESLint for affected files; backend Ruff | Passed |
| Isolated StarRocks with scoped user and active non-default role | INSERT → SELECT → UPDATE → SELECT → DELETE → SELECT passed |
| Temporary Parquet stage | `SELECT * FROM @stage...` and `COPY INTO @stage... FROM records` passed; temporary objects, stage, user, role, and database cleaned up |
| Persisted temporary Agent Studio agent | Saved two custom tools, selected them in one agent, reloaded the agent and tool registry from StarRocks, and executed INSERT → SELECT; agent and tools cleaned up |
| Configured provider, one actual agent turn | Selected INSERT then SELECT, prompted write consent only for INSERT, verified the inserted row, finished with `stop` in 88.69 s |

The real-provider test used the existing provider configuration in memory and an isolated StarRocks test stack for data. The main engine rejected the temporary scoped test user through Ranger 5204 despite native grants; no production data was changed. Both live tests are opt-in and skipped by default.

## Offline benchmark

One local run on 2026-09-23, using a scripted provider and stub query service. It measures Nova/Python overhead, excluding LLM and StarRocks latency.

| Flow | Runs | Median | p95 |
| --- | ---: | ---: | ---: |
| Direct INSERT + SELECT tool pair | 200 | 81 µs | 257 µs |
| Full scripted agent turn with two tools | 100 | 1.20 ms | 4.36 ms |

## Remaining unrelated suite failures

- Full backend unit/eval suite: 3,739 passed, one failed in `test_search_finds_stage_upload_with_browser_owned_file`. That UI-action search test passed 23/23 when its file ran alone; the full-suite failure depends on test order or shared state.
- The latest general `tests.eval.report` run scored 40/41 scenarios (134/135 checks). `change_diagnosis_reconciles_after_data` failed because the numeric-answer validator rejected a `-20` value present in a diagnostic table. This scenario does not exercise custom SQL tools.

The custom SQL tool focused tests and both live integrations passed. These observations validate the exercised SQL categories and boundaries, rather than every possible StarRocks statement or external provider configuration.
