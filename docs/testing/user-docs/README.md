# User documentation acceptance — 2026-11-11

Scope: the 67 user guides served at `/docs`, their navigation/search/renderer,
and product fixes found while following the guides. Local acceptance reused the
existing Nova development stack and authenticated caller paths. Synthetic QA
resources were removed after verification; existing user data was preserved.

## Live acceptance

| Area | Verified behavior |
| --- | --- |
| Docs | Desktop and 320 px layout, menu, search results and empty state, keyboard navigation, anchors, previous/next pages, copy code, sample downloads, document titles and return to Nova. |
| SQL | Database/table DDL, inserts/updates/deletes, queries, joins, grouping, views, materialized-view refresh, role context and refused statements. |
| MySQL clients | Authenticated identity, connection listing, DDL, BEGIN/INSERT/ROLLBACK with zero persisted rows, and SET ROLE. |
| Stages | CSV, TSV and Parquet reads; wildcards; inserts, CTAS and COPY; managed stage authorization; nested-file deletion and sample downloads. |
| Tasks and streams | Multi-statement tasks, manual execution, task graph branches/finalizer, stream changes and consumption, Python tasks, package imports and persisted writes. |
| Notebooks | SQL/Python cells, query results, error output, chart output, runtime metrics and stop; novaduck reads plus append/overwrite/merge with persisted-row assertions. |
| ML | Classification, regression with decimal targets, clustering, anomaly detection and forecasting; training, database-scoped aliases, single/batch prediction and forecast rows. |
| Feature Store | Entity/view/group creation, refresh and version activation, lookup and point-in-time training from a feature group. |
| AI Search | Lexical search, filters, rebuild, READY version activation and search against the new active version. |
| AI SQL | Seven documented function examples with temporary aliases to an existing configured provider; aliases removed after the run. |
| Semantic Views | Builder dimensions and metrics, validation, publication, grouped queries, quality checks and rejected invalid dimensions. |
| Studio | Agent and skill configuration, completed live table/chart response, persisted conversation replay, and Add to Dataviz. |
| Dataviz | Chart creation from SQL, saving into a dashboard, querying the dashboard and publishing an answer chart into the same dashboard while retaining its existing chart. |
| Ontology | Concepts/details, metric bindings, domains, proposal states, checks and releases; no release was changed. |
| Administration | Auth/profile, users, roles, policies, migration capabilities/source inventory, query/load history, cluster health, readiness, audit and AI usage returned successfully (21 read checks). |

The machine-readable [live check record](live-checks.json) contains 204 checks,
including setup and cleanup. It records assertions, not stored credentials or
raw execution payloads. The separate notebook lifecycle smoke completed all its
cells and runtime checks. Browser acceptance used the actual Nova UI; component
tests alone are not evidence of live database or authorization behavior.

## Fixes established by acceptance

- Studio now numbers events after text filtering, preventing precomposed
  table/chart events from being discarded and the run journal from failing.
- Semantic View numeric columns can be explicitly selected as dimensions.
- Regression metrics accept the decimal targets returned by the engine.
- Feature-group training preserves the caller's session/security version.
- Dotted chart fields support sorting without permitting prototype mutation.
- The docs renderer keeps its test fixture unpublished and checks page routes,
  headings, links, language fences and unsafe content.
- Component tests keep BI frame documents local to the test server and unmount
  Studio before restoring request mocks; they do not load the live BI service.

Other included fixes cover role-change errors/context, stage safety, ML scope,
notebook EOF/kernel recovery, and abandoned task settlement. Matching guides
explain the resulting behavior and limits.

## Validation

| Check | Local result |
| --- | --- |
| Backend unit coverage | 9,155 passed; 4 warnings; application coverage 65%. |
| Agent eval scorecard | 54/54 scenarios, 189/189 checks. |
| Stream/journal and security lifecycle regressions | 17 passed. |
| Full-application mypy | Passed; informational untyped-body notes only. |
| Changed-file Ruff | Passed. |
| User-data/system-access boundary checker | Passed. |
| Notebook runtime kernel tests | 39 passed in the runtime image. |
| Frontend lint | Passed, 0 errors and 31 warnings. |
| Frontend production build | Passed. |
| Frontend coverage | 192 files, 1,593 tests passed; line coverage 58.23%. |
| Docs-focused browser tests | 2 files, 15 tests passed. |
| Generated UI validator drift | Passed. |

Commands: `pytest tests/unit --cov=app --cov-branch`,
`python -m tests.eval.report`, `mypy app --no-error-summary`, changed-file
`ruff check --force-exclude`, `python scripts/check_user_data_system_access.py`,
`pnpm lint`, `pnpm build`, `pnpm test:coverage --no-file-parallelism`,
`pnpm exec vitest run --browser.headless src/features/docs`, and `pnpm ui:check`.
Local Python checks used the existing locked virtual environment. Frontend
checks used Node 22 and the installed pnpm toolchain.

## Prerequisites and limits

- Destructive real-engine integration is delegated to GitHub Actions with the
  user's explicit approval. Its seed resets credentials and test data and was
  not run against the shared development stack.
- Lexical search was exercised live. Semantic/hybrid search still needs a
  configured embedding model; no embedding provider was added for this QA.
- External Snowflake/source-cluster migration and MCP execution need working
  connections. PostgreSQL catalog refresh was exercised; unsupported or
  unavailable connections were not represented as successful migrations.
- Automatic scheduled execution needs a configured worker execution account.
  Manual task and graph execution passed; CI covers the seeded scheduler path.
- The local OpenSandbox service intermittently ended an execution stream
  before delivering its completion event. The completed notebook run passed,
  but the transport issue was also reproduced. Nova reports the uncertain
  result and never automatically reruns a potentially mutating cell. This is
  a remaining infrastructure limitation, not a claim of perfect reliability.

## Screenshots

- [Docs desktop](docs-desktop.jpg)
- [Docs at 320 px](docs-mobile.jpg)
- [Semantic View breakdown](semantic-breakdown.jpg)
- [Created dashboard](dataviz-dashboard.jpg)
- [Answer chart added to the existing dashboard](dataviz-publication.jpg)
