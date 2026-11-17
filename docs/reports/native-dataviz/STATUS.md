# Native Dataviz delivery record

Status: **in progress; not production ready; no cutover**. Release QA round 5 (2026-11-17): 181 of 254 pass, 17 out of scope, 56 HIGH open.
Branch: `feat/dataviz-native`.
Baseline: `1ee12694e0ecb6ecfd8135965943be08f5195967`.

## Work packages

| Package | Status | Remaining exit criteria |
| --- | --- | --- |
| WP00 baseline/inventory | DONE — baseline inventory only | Source callers, assets/identities, recognized links, PG epoch, topology and baseline OSS SBOM verified; release distribution/notice checks belong to WP16 |
| WP01 contracts/design | In progress | Full spec coverage, complete compatibility conversion, real shared UI composition |
| WP02 metadata/ACL | In progress | Schema 55 deployed in development (0053 export task handler, 0054 task-login reads and workspace entry insert for scheduled exports, 0055 dashboard comments); final application and release gates remain |
| WP03 datasets | In progress | Table/view, SQL (including declared non-sensitive parameters) and published Semantic View inspection/create/preview/source-update APIs active; parameter declaration editor and preview inputs prepared in unmounted components; local metrics, relationships, sensitive-input engine boundary and product-shell Data tab remain open |
| WP04 calculations | In progress | Exact governed aggregates (sum/avg/min/max/count/distinct, date grains) active; table calculations (running total, percent of total, difference, percent difference, rank, dense rank, moving average) over the governed result, mirrored in scheduled files; ratio/weighted, window calculations, time intelligence and golden results open |
| WP05 chart builder | In progress | Inspector (Configure/Style) and ECharts/KPI/table rendering for 15 visuals including a cross-tab pivot (row/column groups, totals, subtotals) and conditional formatting (colour scale, threshold highlight) for tables and pivots; point map on the street basemap (size by value, colour by group, legend, popups, cross-filter by group, PNG and print from the drawn map); remaining visual QA open |
| WP06 canvas | In progress | 12-column GridStack canvas with drag/resize commits, pages, undo/redo, keyboard nudge, one-column phone view, mounted in Dataviz and Studio Artifacts; Neva cursor overlay and 100-widget load open |
| WP07 filters | In progress | Typed predicate compilation, multi/single value, date range, relative date, number range, text and Boolean controls with governed options; cascading, explicit cross-dataset mapping UI and parameter controls open |
| WP08 interactions | In progress | A mark click offers filter-the-dashboard or drill-down (finer date level or another grouping, narrowed to the clicked value, with breadcrumb back/reset); drill-through to another dashboard (link a chart, open the target narrowed to the clicked value; hidden from people who cannot open the target) |
| WP09 query orchestration | In progress | Client query queue (3 in flight), capacity retry, debounce, abort of superseded queries, epoch-scoped cache keys; server-side cache and large-table pagination open |
| WP10 publish/share | In progress | Publish dialog, Published/unpublished-changes state, share dialog (user/role presets, grants on pinned charts/datasets) and history; multi-role governed E2E and restore UI open |
| WP11 Neva tools | In progress | 11 native tools with approvals, receipts and dashboard_changed refresh; live runs on 2026-11-16: a data question with a verified difference, adding a chart, removing it, and building a shaded pivot with totals; correctable refusals stay in the turn; broader trajectories open |
| WP12 collaboration | In progress | Event-polling refresh (4 s) and Neva change events update open editors; presence (avatars, editing state) and live pointers through a 1 s Redis-backed heartbeat gated by view access, live-tested with two people; websocket replay open |
| WP13 export/report/schedules | In progress | Chart PNG/CSV/Excel, page Excel workbook and a printable report, behind the Export permission with an audited server check; CSV formula guard; scheduled Excel exports of a page run on the task scheduler as the role's scheduled-task account and save into the dashboard's shared workspace; server-rendered PDF/image exports open |
| WP14 conversion | Not started | Complete asset mapping and semantic/visual comparisons |
| WP15 security/resilience | In progress | Shared native dataset read by three people under Ranger row filters (`row-filters-live-attempt-8.json`); reflection policy for every user; faults, restore and metrics open |
| WP16 release QA | In progress | Attempt 2 (`release-qa-attempt-2.json`): 79 PASS after view-only access, multi-value/legend selection, tablet view, chart text alternatives, multi-page reports, table calculations and comments. Attempt 1: 68 PASS, 17 out of scope by the Superset decision, 169 open (31 CRITICAL) with the missing feature, infrastructure or test noted per row in `QA_MATRIX.csv`; five defects found and fixed in the pass |
| WP17 decommission | Done by user decision (2026-11-16), ahead of WP14/WP16 | Superset services, image, gateway route, backend module, frontend and docs removed; development Superset metadata backed up outside Git before deletion; no assets converted |

WP00 completion records the verified baseline, not a product release. WP01–WP17
remain open. Prepared storage/publication mechanics are not completion of the
product-level WP10 flow.

Corrected dependency finding: [finding 1](dashboard-dataset-dependency-finding-1.json)
and [verified correction](dashboard-dataset-dependency-finding-2.json).
The previous publication-based registry tests did not cover a draft-only reference.
The live attempt recorded an incorrect successful delete of its disposable dataset;
the owned dashboard, dataset, roles and source fixtures were cleaned up.

The current batch adds source-independent exact dataset retries, a typed
`datasets.replace` dashboard operation, and atomic dataset creation with its
dashboard reference. PG attempt 28 passes **145 tests, zero skips**, including
upgrade from schema 51, rollback on invalid live references, revoked edit access,
concurrent revision conflict and parent-failure rollback without orphan data.
Live attempt 38 and dataset replay attempt 3 pass after the schema 52 upgrade.
Full backend attempt 14 passed **11,913 tests, zero skips**, changed-file Ruff,
full-application mypy, agent scorecard and user-data-boundary checks, with no
source drift during the run. Frontend attempt 23 passed all
gates and **1,745 tests** before the latest command/client additions; the newer
client/contracts focused run passes 63 tests; source-picker attempt 2 passes five
browser tests after correcting the test-file syntax error retained in attempt 1.
These results do not complete WP03
or establish native editor acceptance. At that point the 254-scenario matrix
had six passed and 248 not run.

The next UI batch prepares the shared Data tab, source selectors, Monaco SQL
input, authoritative field/lineage display, atomic save/retry form and explicit
preview. These components remain unmounted in the product shells. Native browser
attempt 6 passed 100 tests; Data-tab/index attempt 1 passed 109 tests, TypeScript
and lint (one Fast Refresh warning, subsequently removed by separating the shared
query hook). Frontend attempt 24 passed build and 1,779 tests across 208 files,
including expanded rapid-input and role-switch regressions, but failed one test-file
lint rule; that formatting was corrected. Attempt 25 passed install, lint and build,
then completed with 1,790 passes and one failed consent fixture in Studio. Its finite
response closed before the pending decision; a cancellable open response fixes the
fixture while preserving the assertions and checking the decision request. The
focused Studio regression passes 66 tests. Attempt 26 passed install/lint/build but
was cancelled during host pressure after multiple browser timeouts; it is not a
pass. Attempt 27 was also cancelled after host pressure and browser timeouts
persisted with serial browser files and one worker. Source, assertions and
timeouts stayed unchanged; the full frontend gate remains pending. Earlier browser attempts retain the
test-cleanup iframe navigation, invalid inspection fixture and contract-default
assertion failures, plus the rapid Monaco input loss corrected in source.

Exact historical draft reads and the bounded dashboard dataset-name index are
active APIs. PG attempt 29 passed 79 native integration tests; attempt 30 passed
the three historical-read/index cases, including child permission revocation.
Governed live attempt 39/dataset replay attempt 4 passed through the running
gateway with owned fixture cleanup. Attempt 40/replay 5 and PG attempt 31 verify
previewing an older pinned revision after a newer draft is saved. Backend attempt
15 completed with 11,924 passes and one failure: the parameter-safety gate found
two unnecessary SQL casts in the dataset-name index. Removing them preserves
typed Python UUID/integer arrays. The unchanged guard and 78 related tests pass;
PG attempt 32 passes the three affected historical-read/index cases. Full backend
regression must run again on the corrected source.

Live attempt 41 failed initial source reflection after the empty-database readiness
probe, before dataset acceptance began. Cleanup passed. The fixture now probes the
created table and view with bounded read-only retries before acceptance; DDL is
never replayed. Attempt 42 and dataset replay 7 pass with cleanup. Its first
reflection probe succeeded, so policy propagation remains a hypothesis for the
earlier failure rather than a proven cause.

Dataset reuse is now prepared in the shared Data tab. The focused browser run
passed 75 tests covering revision selection, permission refusal, pagination,
unknown outcomes and concurrent additions. Type checking initially rejected
`Array.at` under the project's target; indexed access replaces it. This remains
component evidence, not product-shell or E2E acceptance.

Configured-secret scan 9 found an isolated fixture password in two compressed
failed PG regression artifacts. The restricted originals are retained outside
Git; public artifacts were redacted, with original/new hashes recorded in
`evidence-redaction-attempt-9.json`. Scan 10 passed across 988 owned files including
decompressed gzip. The failed scan remains recorded; subsequent artifacts require
another scan. No task commit or push has occurred.

The existing QueryService row sink now supports bounded parameter queries. Focused
unit coverage passes 177 tests; full mypy and changed-file Ruff pass after the
recorded formatting correction. Real QueryService acceptance passes for Alice
and Bob, including Ranger filtering/masking, exact streamed decimal values,
stream overrun refusal and a successful subsequent managed session. This does
not complete dataset parameter authoring or engine-side sensitive-value privacy.

The parameter batch adds explicit decimal precision/scale, typed schema
inspection without input values, persisted SQL parameter declarations, enum/range
validation and transient preview bindings through QueryService. Focused attempt 2
passed 420 tests; full-application mypy then failed at `contracts/parameters.py:30`
on scalar union narrowing (retained in `dataset-parameters-mypy-attempt-1.txt`).
The conversion now narrows on the discriminated `StringValue | BooleanValue`
models; mypy reports no issues in 757 files.

**16 November 2026 parameter verification batch.** All results below cover the
final source of this batch unless stated.

- Real-engine QueryService parameters (`parameter-types-live-{alice,bob}-attempt-3..6`):
  eight consecutive passes, 30 checks for Alice and 28 for Bob, including Ranger
  row filters and masks, inactive-role refusal, exact streamed decimals and
  injection-shaped values. Attempt 2's lost correlation (Alice) and metadata
  timeout (Bob) did not reproduce. Both coincided with severe host pressure
  (load average above 250 in the cancelled frontend attempts; worker logs show
  PG statement timeouts in the same period). The root cause is **not established**;
  QueryService intentionally swallows correlation-telemetry errors, which hides
  the underlying exception.
- Parameterized SQL dataset through the normal HTTP API
  (`parameter-dataset-http-attempt-6.json`, `-7.json`, fixture attempts 6–7):
  77 checks each, on an owned database/role created by the source-identity
  fixture: undeclared-name refusal, declarations echoed and stored, changed
  declaration refused as stale, text-list/decimal/date bindings with defaults,
  ordered exact decimal and leap-day date values, inclusive typed comparisons,
  missing/wrong-type/below-minimum/invalid-date/unknown-name refusals, an
  injection-shaped value returning no rows without echo, outsider refusal and
  value-free query history. Dataset soft-deleted; fixture database, roles and
  policies removed; QA grants and Ranger attributes unchanged.
  Retained failures: attempt 1 (the QA role has no schema-inspection right on
  `analytics.sales`, an intended boundary), fixture attempt 1 (Ranger credential
  wiring in the runner; nothing created), attempts 2 and 5 (504 at the 30-second
  source deadline while the full backend/frontend suites loaded the host) and
  attempt 4 (test input omitted a declaration; the server correctly refused it).
- The `/docs` example query ran as Alice through QueryService
  (`parameter-docs-example-attempt-1.json`): success, Ranger-visible rows only.
- Backend gate attempt 16 (`backend-gates-attempt-16.json`): **11,995 unit tests
  passed, zero failures/skips**; full mypy, agent scorecard and user-data
  boundary passed. The first Ruff step wrongly included generated ANTLR grammar
  files and failed; the corrected changed-file Ruff over 70 files passed. Live
  verifier scripts changed after this run and pass Ruff separately.
- Frontend gate attempt 28 (`frontend-gates-attempt-28.json`, Node 22.22.0 /
  pnpm 11.8.0): frozen install, lint (0 errors), build and **1,801 tests in 210
  files** passed. Two native tests expected requests without the contract's
  default `parameters: []` and were updated to the validated wire shape.
- New browser tests: 8 for preview parameter inputs (typed values, list add/remove,
  defaults, NULL only when declared, sensitive refusal, phone width) and 2 for the
  new SQL parameter declaration editor (validation before any request, inspect and
  save the analyzer-accepted declarations, stale-inspection reset, saved-preview
  inputs, 360 px dark layout). These components are still not mounted in the
  Dataviz or Studio shells, so this is not product E2E acceptance.
- QA matrix: NDV-DATA-007 and NDV-DATA-008 are `PASS`; **8 PASS, 246 NOT_RUN**.
- Configured-secret scan over the 40 new evidence files: zero matches for the
  QA fixture passwords and the Ranger admin credential. Not a full secret audit.
- Dependency audit (`pnpm audit`): unchanged at 0 critical, 0 high, 4 moderate,
  2 low. All are dev/test tooling (esbuild dev server, smol-toml via knip,
  @vitest/mocker, minimatch via ESLint) except KaTeX (low, prototype-pollution
  hardening). Upgrades are not made in this batch.

Open from this area: a native editor mount for the Data tab, sensitive parameters
(engine log/process-list protection), allowed-value/range editing in the
declaration UI, parameter controls bound to dashboard filters (WP07), and preview
resilience under host load (two 504s at the 30-second deadline). WP01–WP17
remain open and no Superset cutover has occurred.

## 16 November 2026 product batch: native dashboards active

Native Dataviz was then the default **Dataviz** mode in Studio, with Superset
reachable as **Legacy BI** (removed later the same day, below).

- Backend: `POST /viz/datasets/{id}/query` plans one governed aggregate over the
  dataset source (`viz/aggregate.py`): quoted catalog identifiers only, typed
  filter values bound as engine parameters (eq/neq/in/not_in/comparisons/between/
  null/text contains/relative dates with fiscal and week starts), exact decimal
  widening for sums/averages, date grains, deterministic order and limit+1
  truncation. The same source-identity and stale-schema checks as previews run
  before and after execution under the caller's active role. Pinned publication
  revisions are readable through revision row security for viewers.
- Live evidence: `aggregate-query-http-attempt-1.json` (111 checks; owned fixture,
  cleanup and QA identity verified).
- Frontend: dashboard list/create, Canvas/Data editor, GridStack canvas, chart
  inspector with live preview (14 visuals), dashboard filters, cross-filtering,
  publish/share/history, query queue with capacity retry, phone layout; also
  opened from Studio Artifacts with the same editor.
- Neva: native tools for the Dataviz assistant (approval-gated writes, receipts,
  `dashboard_changed` refresh). Live run: the first attempt failed (dataset
  revision confusion) and the second added a chart after approval.
- UI QA walkthrough: `native-dashboard-ui-qa-attempt-1.json`, including four
  defects found and fixed (Neva revision resolution, redundant color split,
  capacity refusals under rapid filtering, phone header/band overflow) plus the
  inclusive end-day date-range defect found during QA review.
- QA matrix: 15 PASS, 239 NOT_RUN.

## Evidence recorded so far

| Evidence | Result | Scope |
| --- | --- | --- |
| `contracts-attempt-1.xml` | 53 passed | Backend model, layout, operations and compatibility tests at that attempt |
| `browser-contracts-attempt-1.txt` | 8 passed | Browser schema validation, not editor E2E |
| `pg-attempt-1.xml` | 1 failed | Ambiguous PL/pgSQL sequence identifier; retained and corrected |
| `pg-attempt-2.xml` | 9 passed | Initial native document PG17 integration |
| `pg-attempt-3.xml` | 11 passed | Added self-revocation and expiry replacement |
| `pg-attempt-4.xml` | 17 passed | Added direct-SQL receipt/revision attacks, cancellation and workload restrictions |
| `mypy-attempt-1.txt` | Failed | Narrow visualization Literal typing; corrected |
| `mypy-attempt-2.txt`, `mypy-attempt-3.txt` | Passed | Full application type checking at those attempts |
| `typescript-attempt-1.txt` | Failed | Generated JSON schema typing at Zod boundary; corrected |
| `typescript-attempt-2.txt` | Passed | TypeScript build checking at that attempt |
| `ruff-attempt-3.txt` | Failed | New-file style errors; corrected |
| `ruff-attempt-4.txt` | Passed | Changed-file Ruff at that attempt |
| `contracts-coverage-attempt-3.txt` | 88 passed; 89% combined coverage | Contract/binding/operation tests; ACL expansion 100%, operations 99%; semantic/filter branch target not yet met |
| `pg-attempt-5.xml` | 38 passed | Latest native document references and existing migrator suite on PG17 |
| `pg-regression-attempt-1.xml.gz` | 712 passed, 303 errors | Fixture PostgreSQL child exited and database recovered; not a passing regression |
| `backend-unit-attempt-1.xml.gz` | 11,362 passed, 1 failed | ML hard-timeout process failed its next startup deadline during the full run |
| `ml-timeout-recheck-attempt-1.txt` | 1 passed | Unchanged failing test passed in isolation; does not replace a passing full run |
| `scorecard-attempt-1.txt` | 54 scenarios, 189 checks passed | Before the generated Zod validator update; affected checks must run again |
| `frontend-gates-attempt-4.txt` | Lint/build/coverage passed; 199 files, 1,663 tests | Includes generated contracts and real GridStack browser compatibility; no native product E2E yet |
| `ruff-attempt-10.txt`, `mypy-attempt-10.txt` | Passed | Changed Python files and full application (745 files) |
| `frontend-audit-attempt-2.json` | 0 critical, 8 high, 22 moderate, 9 low | Zod and Seroval findings fixed; remaining dependency findings still open |
| `backend-sbom.cdx.json` | Generated and schema-validated | 141 environment components; three packages need license metadata enrichment |
| `inventory-integrity.json`, `BASELINE_AUDIT.md` | 41 snapshot counts, hashes and private file permissions verified | Baseline assets, identities and recognized links; two unresolved legacy references |
| `license-audit.json`, `license-metadata-enrichment.json` | 880 components inventoried; six exact-version license enrichments | License metadata complete; distribution/notice review remains open |
| `references-attempt-3.txt` | 106 passed | Bounded graph traversal, shared subgraphs, typed control defaults and immutable digest compatibility |
| `pg-regression-attempt-2.xml` | 1,018 passed; no skips | Full CI-selected PG17 suite before the new document-read methods |
| `pg-attempt-6.xml` | 43 passed | Latest native storage and migrator; publication-safe listing, bounded history/replay, session-bound receipt lookup and revoked grants |
| `backend-unit-attempt-2.xml.gz` | 11,416 passed; 65% application coverage | Passing full run before the subsequent document-read methods; source fingerprint retained |
| `scorecard-attempt-2.txt` | 54 scenarios, 189 checks passed | Includes updated generated Zod validator |
| `frontend-gates-attempt-5.txt` | Lint/build/coverage passed; 199 files, 1,663 tests | Current generated schema and pinned GridStack compatibility |
| `ruff-attempt-15.txt`, `mypy-attempt-15.txt` | Passed | Latest document-read implementation; full application type check |
| `presentation-attempt-2.xml` | 129 passed; 89% combined coverage | Decimal axis/reference bounds, Top N and table-calculation bindings, per-widget overrides and sensitive parameter allowlists |
| `pg-attempt-7.xml` | 44 passed | Current native contracts, reads and migrations; includes cross-dataset widget-filter rejection |
| `agent-trajectories-attempt-1.xml` | 358 passed | Existing assistant trajectory suite; not live Neva native-tool acceptance |
| `ruff-attempt-18.txt`, `mypy-attempt-18.txt` | Passed | Current Python source and full application type check |
| `user-data-boundary-attempt-1.txt` | Passed | Existing system/root shortcut detector |
| `configured-secret-scan.json` | No configured development secret matches | 153 artifact/contract files at the scan; values are never recorded |
| `frontend-gates-attempt-6.txt` | Lint/build passed; coverage run failed with two timeouts | 1,661 passed, 2 timed out in unrelated provider/trace browser tests; screenshots retained |
| `frontend-timeout-recheck-attempt-1.txt` | 6 passed | Same tests, assertions and timeout with one worker; full coverage rerun pending |
| `backend-unit-attempt-3.xml.gz` | 11,437 passed, 2 failed | The typed-parameter gate rejected two new cursor casts; the existing ML process startup timed out under load |
| `recheck-attempt-4.txt` | 132 passed | Native contracts, typed-parameter boundary and unchanged ML timeout case after replacing cursor casts with fixed optional clauses and typed Python values |
| `pg-attempt-8.xml` | 44 passed | Native storage and migrator after the cursor fix; no skipped cases |
| `ruff-attempt-19.txt`, `mypy-attempt-19.txt` | Passed | Changed-file lint and full application type check after the cursor fix |
| `frontend-coverage-attempt-7.txt` | Cancelled, exit 130 | Browser parallelism remained enabled despite the generic worker limit; observed host load and failures retained separately |
| `frontend-coverage-attempt-8.txt` | Incomplete, 93 tests passed before an unhandled iframe navigation error | Serial browser run reached sign-in outside the test context; this is not a passing coverage gate |
| `frontend-studio-isolation-attempt-1.txt` | 29 passed | The affected Studio file passed unchanged in isolation with browser file parallelism disabled; full regression remains required |
| `development-readiness-recheck-2.txt` | Passed | Development web, query and frontend ready after the recorded PostgreSQL process recovery; earlier failed readiness attempts retained |
| `pg-attempt-9.xml` | 50 passed | Personal workspace binding and session-admitted command service on schema 49 |
| `frontend-coverage-attempt-9.txt` | Failed | Uncancelled capability batch escaped the Studio test lifecycle; fixed in the real hook with focused cancellation tests |
| `frontend-coverage-attempt-10.txt` | Failed | Map tests retained callbacks and depended on public tile loading; explicit cleanup and local basemap added for geometry/state checks |
| `frontend-renderer-recheck-attempt-1.txt` | 47 passed | Renderer, capability cancellation and docs checks; live public tile availability remains separate |
| `frontend-coverage-attempt-11.txt` | 200 files, 1,668 tests passed | Full serial browser coverage after cancellation and fixture fixes; 58.17% statements, 59.05% lines |
| `native-http-unit-attempt-1.txt` | 40 passed, 1 failed | Body-size exception was converted to HTTP 400 by FastAPI; changed to its preserved HTTP exception boundary |
| `native-http-unit-attempt-2.txt` | 41 passed | Authentication admission, header validation, chunked size limits, input redaction and normal route registration |
| `pg-attempt-10.xml` | 40 passed, 2 failed, 13 errors | Grants denial ordering and concurrent migrator teardown defect; both fixed, later verification required |
| `pg-fixture-cleanup-attempt-10.json` | Owned fixture cleanup verified | First cleanup refused before mutation; exact role allowlist then removed the residual disposable schema/roles |
| `development-readiness-schema-49-attempt-5.txt` | Passed | All development runtimes recovered after the recorded PG interruption; no container or volume changes |
| `native-http-runtime-attempt-1.json` | Passed, limited scope | Live native route registration and anonymous refusal; no authenticated native UI/query flow claimed |
| `pg-attempt-11.xml` | 52 passed, 10 failed | Definer row-lock RLS and viewer write-denial ordering; corrected before schema 50 deployment |
| `pg-attempt-12.xml` | 61 passed, 1 failed | Publication/edit test combined two commands in one transaction; corrected to separate committed operations |
| `pg-attempt-13.xml` | 63 passed, no skips | Native HTTP, dependency deletion races, immutable revisions and schema-49 upgrade to schema 50 |
| `development-schema-50.json`, `development-readiness-schema-50-attempt-1.txt` | Upgrade and readiness passed | Verified backup, preserved epoch/table counts and all development runtime roles ready |
| `backend-unit-attempt-4.xml.gz` | Cancelled after 6,435 passed | Review found a legacy-role fallback in native session admission; stopped to fix and test before restarting the full gate |
| `ruff-attempt-21.txt`, `native-http-unit-attempt-3.xml` | Passed | Native session admission rejects absent, empty or malformed canonical role assignments without falling back to legacy roles |
| `pg-attempt-14.xml` | 64 passed, no skips | Includes revoked canonical roles with stale legacy role data; fixture PG17 only |
| `frontend-native-client-attempt-1.txt` | Type check failed | Generated permission schema needed the existing JSON Schema type boundary |
| `frontend-native-client-attempt-2.txt` | 61 passed, 1 failed | Test incorrectly assumed JSON member ordering; corrected to compare the complete decoded command |
| `frontend-native-client-attempt-3.txt` | 63 passed | Shared transport, generated contracts, conflict/maintenance errors, cancellation, role/login changes, renewal races and docs content |
| `frontend-native-client-attempt-4.txt` | 45 passed | Latest client UUID canonicalization, API transport and rendered docs checks |
| `backend-unit-attempt-5.xml.gz` | 11,521 passed, no skips | Full suite before the workspace-admission audit extension; 66.57% line coverage, 59.23% branch coverage |
| `scorecard-attempt-3.txt`, `user-data-boundary-attempt-2.txt` | Passed | 54/54 scenarios and 189/189 checks; existing system-access boundary detector |
| `native-admission-unit-attempt-1.txt` | 52 passed | Denied workspace resolution and stale sessions audit once before any document transaction, without private names or definitions |
| `native-admission-ruff-attempt-1.txt`, `native-admission-mypy-attempt-1.txt` | Passed | Changed-file Ruff and full application types after the admission audit fix |
| `docs-visual-attempt-2.json` | Passed, limited scope | Authenticated Chrome inspection of the three changed docs pages and their native-document link, desktop dark theme |
| `native-http-live-attempt-1.json` | 38 assertions passed | Live StarRocks-authenticated Alice/Bob sessions, personal isolation, command replay, publication, concurrent CAS, restore, event history and deletion receipts on development schema 50; owned fixture soft-deleted and both QA sessions logged out |
| `live-script-ruff-attempt-1.txt`, `live-script-ruff-attempt-2.txt` | Initial line-length failure corrected; second check passed | Reusable live metadata acceptance script; no credentials or tokens in evidence |
| `frontend-gates-attempt-12.json`, `frontend-coverage-attempt-12.txt` | Lint/build/coverage passed; 201 files, 1,693 tests | Current generated HTTP contracts, native document client and shared authentication transport; 59.18% line coverage |
| `source-schema-unit-attempt-1.xml`, `source-schema-mypy-attempt-1.txt` | 25 tests and full application mypy passed | Catalog-owned physical identity and schema observations; missing, ambiguous, replaced and unsupported sources are refused |
| `source-schema-ruff-attempt-1.txt`, `source-schema-ruff-attempt-2.txt` | Initial import/line-length errors corrected; second check passed | Shared catalog changes and live metadata script |
| `view-identity-build-attempt-1.json` | Compose validation and image build passed | Pinned 4.1.4 FE patch compiled with existing Ranger/task/JDBC patches and build-time Snowflake regressions |
| `view-identity-deployment-attempt-1.json`, `view-identity-deployment-attempt-2.json` | Failed/incomplete | Docker initially missed the old FE exit event, then retained the replacement's temporary name; no OOM kill reported |
| `view-identity-deployment-attempt-3.json` | Healthy; original volumes preserved | Reconciled the stopped old container and restored the replacement's intended name; no second engine stack |
| `patched-fe-role-regression-attempt-1.txt` | Passed | Live proxy active/inactive roles, simultaneous role isolation, transaction role preservation, row filtering, masking, unassigned-role and root refusal |
| `patched-fe-readiness-attempt-1.txt`, `patched-fe-readiness-attempt-2.txt` | Initial web/query readiness delay; second command exited 0 | Development runtime recovery after FE recreation |
| `source-identity-live-attempt-1.json` through `source-identity-live-attempt-5.json` | Failed, retained | Probe port, fixture policy resource type, metadata reflection, duplicate Ranger policy scope and missing view-object permission corrected; existing QA grants/attributes preserved |
| `source-identity-live-attempt-6.json`, `source-identity-live-attempt-7.json` | Failed, retained | Decimal metadata spelling and unsupported `ALTER VIEW RENAME` test assumptions corrected; observed integer display width led to a shared type-parser fix |
| `source-schema-unit-attempt-2.xml` | 104 passed | Shared catalog, integer width/type binding and existing SQL hardening/readiness regressions |
| `source-identity-live-attempt-8.json`, `source-identity-live-attempt-9.json` | 18 and 20 assertions passed | Real table/view IDs, empty-source integer/decimal/nullability schema, stable metadata, table rename, view drop/recreate, inactive/revoked roles and engine NONE/ALL refusal; owned fixture cleaned up |
| `backend-unit-attempt-6.xml.gz`, `scorecard-attempt-4.txt`, `user-data-boundary-attempt-3.txt` | 11,541 passed; scorecard and boundary checks passed | Admission audit and source-schema changes; collected before the integer display-width and named-parameter changes |
| `pg-regression-attempt-3.xml.gz` | 1,046 passed, no skips | All 33 applicable CI PostgreSQL suites on the isolated PG17 fixture, including native metadata and admission-audit tests; 2,703.85 seconds |
| `evidence-redaction-attempt-1.json`, `evidence-redaction-attempt-2.json` | Published JUnit fixture values redacted | Credential-regression parameter IDs matched configured development values; assertions/results unchanged, private original archives retained outside Git |
| `configured-secret-scan-attempt-3.json` | No exact matches in 421 changed/untracked files | Includes decompressed archives; exact configured-value scan only, not a comprehensive secret detector |
| `parameters-unit-attempt-1.xml` | 93 passed | Typed parameter values, grammar, planning, read-only validation and QueryService transport |
| `parameters-unit-attempt-2.xml`, `parameters-unit-attempt-3.xml` | Initial 94 passed/3 failed; then 97 passed | Driver tests incorrectly expected backtick quoting for an ordinary validated role name; corrected expected existing syntax; failure redaction and cancellation verified |
| `parameters-regression-attempt-1.xml`, `parameters-regression-attempt-2.xml` | 358 and 362 passed | Parser, binding, planner, executor, unchanged consumers, grammar, streaming and Autopilot regressions |
| `parameters-mypy-attempt-1.txt`, `parameters-mypy-attempt-2.txt` | Initial call-argument type failure; then all 750 app modules passed | Explicitly pass the existing transaction boolean when dispatching script statements |
| `parameters-ruff-attempt-1.txt` through `parameters-ruff-attempt-4.txt` | Formatting/import issues corrected; latest passed | New parameter implementation, focused tests and live probe |
| `parameter-grammar-drift-attempt-1.txt`, `parameter-grammar-reproducibility.json` | Passed | Pinned upstream drift and byte-identical canonical ANTLR regeneration; map/slice syntax retained |
| `parameters-live-attempt-1.json`, `parameters-live-attempt-2.json` | Failed, retained | Probe used the wrong port variable, then assumed asterisks for the existing mask; corrected using the ordinary governed query as reference and Alice's separately authorized raw-role comparison |
| `parameters-live-attempt-3.json`, `parameters-live-attempt-4.json` | 20 and 18 assertions passed | Alice/Bob row isolation, list binding, injection text, int64/decimal/date/timestamp/boolean, masking, denied role, durable audit and parameter-free Nova history |
| `backend-unit-attempt-7.xml.gz` | 11,647 passed, 1 failed | Legacy Stage test constructed a partial namespace without the new context field; product code uses the complete typed context |
| `parameters-stage-regression-attempt-1.xml` | 116 passed | Stage fixture now uses the real `ExecutionContext`; all existing redaction assertions retained |
| `mypy-attempt-23.txt`, `ruff-attempt-23.txt` | Passed | All 750 app modules and every owned changed Python file, excluding generated ANTLR artifacts |
| `dependency-security-provenance.json`, `frontend-audit-attempt-3.json` | 0 critical, 0 high, 4 moderate, 2 low | Updated Vite, DOMPurify, source-map-js, follow-redirects and the official Apache-2.0 SheetJS package with verified tarball integrity; unresolved advisories retained |
| `workbook-export-attempt-1.txt` | 2 passed | Real XLSX round-trip preserves Unicode, exact numeric strings, sheet order and formula-shaped strings without executable formula/link metadata |
| `frontend-gates-attempt-13.json` | Frozen install, lint, build and coverage passed; 202 files, 1,695 tests | Dependency security updates and real workbook regression tests; 59.20% line coverage |
| `backend-unit-attempt-8.xml.gz`, `scorecard-attempt-6.txt`, `user-data-boundary-attempt-5.txt` | 11,650 passed, zero failures/skips; scorecard and boundary passed | Full source before dataset authoring; 66.82% line and 59.55% branch coverage |
| `backend-unit-attempt-9.xml.gz`, `scorecard-attempt-7.txt`, `user-data-boundary-attempt-6.txt` | 11,697 passed, zero failures/skips; scorecard 54/54 scenarios and 189/189 checks; six boundary tests passed | Dataset authoring source; 66.85% line and 59.58% branch coverage; source fingerprint in `backend-source-attempt-9.json` |
| `parameters-stage-regression-attempt-3.xml`, `parameters-live-attempt-5.json`, `parameters-live-attempt-6.json` | 118 focused tests, 20 and 18 live assertions passed | Final parameter profile refusal, SQL CPU offload, borrowed-session rejection and governed Alice/Bob execution |
| `parameter-proxy-regression-attempt-1.json`, `ruff-attempt-24.txt`, `mypy-attempt-24.txt` | Passed | Current parameter consumer/role regression and lint/types before dataset authoring |
| `frontend-sbom-security-update.cdx.json`, `license-metadata-security-update.json` | 736 frontend components; all have license metadata | Exact installed versions enriched five missing MIT declarations; distribution review remains open |
| `evidence-redaction-attempt-3.json`, `evidence-redaction-attempt-4.json` | Published JUnit fixture values redacted; private originals retained | Unit attempts 7 and 8 each contained seven configured fixture values; test results unchanged |
| `dataset-authoring-unit-attempt-1.xml` | Collection failed | Reserved pytest fixture name; renamed without weakening assertions |
| `dataset-authoring-unit-attempt-2.xml`, `dataset-authoring-unit-attempt-3.xml`, `dataset-authoring-unit-attempt-4.xml` | 126, 129 and 130 tests passed | Catalog schema/identity, independent SELECT authorization, cancellation/deadline, source races, role changes, server-derived persistence and API admission |
| `dataset-live-attempt-1.json` | Failed, retained | Existing QA default role lacked catalog reflection; no native dataset created and both sessions logged out |
| `dataset-live-attempt-2.json`, `source-identity-live-attempt-10.json` | Passed; owned resources cleaned up | Explicit temporary fixture role, empty view schema, PG document creation/replay/publication/isolation; original QA grants and attributes preserved |
| `dataset-client-types-attempt-1.txt`, `dataset-client-tests-attempt-1.txt` | Type check and 54 browser tests passed | Shared source inspection/create/read transport, mismatched replies, generated contracts and docs |
| `docs-visual-attempt-3.json` | Passed, limited scope | Updated native-document page, anchors and feature limits; authenticated desktop Chrome, dark theme |
| `dataset-live-attempt-3.json`, `source-identity-live-attempt-11.json` | Passed; owned resources cleaned up | Also verifies inactive-role source refusal with redacted 403; exact authorized role activation and existing grants/attributes preserved |
| `ruff-attempt-25.txt`, `mypy-attempt-25.txt`, `contracts-drift-attempt-9.txt` | Passed | Current dataset source, scripts and generated browser contract drift |
| `frontend-gates-attempt-14.json` | Frozen install, lint, build and coverage passed; 202 files, 1,700 tests | Dataset browser transport and updated docs; 59.23% line coverage |
| `configured-secret-scan-attempt-4.json` | No exact matches in 523 owned files | Includes uncompressed evidence contents; later generated files need the scan again |


| `query-schema-build-attempt-1.txt`, `query-schema-build-attempt-2.txt` | Failed, retained | Corrected pinned parser API, integer display-width and unsupported array-slice test assumptions |
| `query-schema-build-attempt-3.json` | Image built; all 15 Java tests passed | First physical-expression schema implementation; live nullability evidence below requires an implementation correction |
| `query-schema-deployment-attempt-1.json` | FE healthy; existing volumes preserved | Existing Compose project, no added FE/BE; image `d66e88b035c35d7aef0fc4327773b619be6e2d9c48335848a64da32e4be0445a` |
| `query-schema-runtime-attempt-1.json` | Host web/query readiness passed after reload | Initial auto-reload raced the FE restart and failed startup; no metadata reset |
| `query-schema-live-attempt-1.json` | Failed, retained | HTTP startup timeout; owned fixture removed; no login token created |
| `query-schema-live-attempt-2.json`, `query-schema-live-attempt-3.json`, `query-schema-live-attempt-4.json` | Failed, retained | Typed empty-view and computed outputs passed; physical-plan nullability failed the join oracle. Patch changed to analyzed fields; new build/live acceptance required |
| `query-schema-unit-attempt-1.xml`, `query-schema-unit-attempt-2.xml`, `query-schema-unit-attempt-4.xml` | First attempt: 3 failed / 172 passed; later 41 and 185 passed | Corrected test planning-context API; typed schema statement, rejection rules, decoder and scoped CTE dependency extraction |
| `query-schema-regression-attempt-1.xml` | 582 passed | All SQL frontend and grammar unit modules at this source |
| `sql-dataset-unit-attempt-1.xml` | 187 passed | SQL source dependency binding, authoritative output metadata, immutable digest, schema races, stale create, no external I/O within PG transaction |
| `ruff-attempt-26.txt`, `mypy-attempt-26.txt`, `ruff-attempt-27.txt`, `mypy-attempt-27.txt` | First attempts failed; subsequent checks passed | Fixed annotation and line lengths; full application mypy checked 752 files |
| `sql-dataset-client-tests-attempt-1.txt`, `sql-dataset-client-tests-attempt-2.txt`, `sql-dataset-client-types-attempt-1.txt` | First browser run: 1 failed / 38 passed; retry 39 passed; types passed | Added explicit server default fields to fixture; authenticated SQL inspect/create transport uses existing engine/API |


| `query-schema-build-attempt-4.json` | Failed, retained | Maven artifact download ended with HTTP/2 error; added bounded HTTP/1.1 retries while retaining SHA-256 verification |
| `query-schema-build-attempt-5.json` | Built; all 16 Java tests passed | Corrected analyzed-field schema; regression separates semantic nullability from optimized expressions |
| `query-schema-deployment-attempt-2.json` | Failed, retained | Upstream `stop_fe.sh --graceful` terminated the existing FE container with signal exit 138 (exec exit 137), without OOM; no claim of graceful shutdown. Existing deployment is recreated in attempt 3 |
| `query-schema-grammar-drift-attempt-1.json`, `sql-dataset-unit-attempt-2.xml` | Byte-identical regeneration; 188 passed | Canonical ANTLR 4.13.2 artifacts, SQL identity stability across comments/terminators |


| `query-schema-deployment-attempt-3.json`, `query-schema-runtime-attempt-2.json` | Healthy FE and web/query processes; volumes preserved | Corrected analyzed-field implementation deployed through existing project; graceful shutdown defect remains open |
| `query-schema-live-attempt-5.json` | Failed test expectation, retained | Corrected schema/nullability passed; external export was safely refused with HTTP 403 instead of the test's expected HTTP 200 error envelope |
| `query-schema-live-attempt-6.json`, `source-identity-live-attempt-17.json` | Passed; 65 schema checks and 26 fixture checks; no cleanup errors | Empty/computed types, stable outer-join nullability, real unmatched NULL, DML/effect refusal, active/NONE/ALL/revoked roles, unchanged original grants/attributes |
| `sql-dataset-live-attempt-2.json`, `dataset-live-attempt-8.json` | Passed; 48 and 47 checks; owned datasets soft-deleted | Real HTTP SQL/view authoring, authoritative bindings/schema, stale rejection, replay, publication and private access |

| `frontend-gates-attempt-15.json` | Frozen install, lint, build and coverage passed; 202 files, 1,703 tests | SQL dataset client and docs; 59.24% line coverage. Final wording change requires focused docs verification |
| `query-schema-proxy-regression-attempt-1.json` | Seven checks passed | Current MySQL proxy exposes typed schema; row filter, role-scoped masks, named-role refusals retained |
| `parameters-live-alice-query-schema-regression-1.json`, `parameters-live-bob-query-schema-regression-1.json` | Passed | Current FE retains governed parameter execution; engine log redaction remains unverified |
| `query-schema-doc-example-attempt-1.json`, `docs-visual-attempt-4.json` | Passed | Exact schema example executed live; three updated docs pages visually verified in authenticated Chrome |
| `query-schema-patch-verify-attempt-3.txt`, `grammar-pin-attempt-3.txt` | Passed | Patch applies to pinned upstream; Nova grammar markers corrected after retained pin-check failure |
| `schema-host-restart-attempt-1.json` | Web/query/frontend ready after existing dev supervisor restart | Existing checkout and services; proxy restarted to load canonical grammar |

| `dataset-preview-unit-attempt-1.xml`, `dataset-preview-unit-attempt-2.xml`, `dataset-preview-unit-attempt-3.xml` | 136, 222 and 227 passed | Exact scalar serialization, NULL/DST rejection, bounded stream/deadline, stale schema/role/metadata and admission; ordering added after attempt 3 and covered by subsequent full gates |
| `dataset-preview-client-tests-attempt-1.txt`, `dataset-preview-client-tests-attempt-2.txt`, `dataset-preview-client-types-attempt-1.txt` | 62 and 54 tests passed; types passed | Browser preview identity/revision/type/pagination checks, docs, and actual Vite routing to query vs web upstreams |
| `source-identity-live-attempt-18.json`, `dataset-preview-live-attempt-1.json` | Passed, owned source fixture removed and dataset soft-deleted | 24 fixture assertions and 42 preview assertions; precise typed pagination and permission refusals through web endpoint before workload routing |
| `source-identity-live-attempt-19.json`, `dataset-preview-live-attempt-2.json`, `dataset-live-attempt-10.json`, `sql-dataset-live-attempt-4.json` | Passed, cleanup verified | Current ordered preview through Vite and the existing query workload; metadata mutations through web; source privileges unchanged |
| `ruff-attempt-29.txt`, `ruff-attempt-30.txt`, `mypy-attempt-29.txt`, `mypy-attempt-30.txt`, `contracts-drift-attempt-11.txt` | First Ruff failed; corrected Ruff/types/contract drift passed | Typed preview, bounded streaming and admission at this source |

| `preview-stream-refusal-live-attempt-1.json` | Failed, retained | Closing asyncmy's socket alone caused cursor cleanup to replace a size refusal with AttributeError |
| `preview-stream-refusal-live-attempt-2.json`, `preview-stream-unit-attempt-1.xml` | Live refusal/cancellation passed; focused unit passed | Disarm the pinned driver's unread result before socket close; exact 422/413 and CancelledError preserved |
| `dataset-preview-gateway-config-attempt-1.json`, `dataset-preview-gateway-config-attempt-2.json` | First isolated parse failed; unprivileged parse passed | Initial root process lacked chown capability; same routes validated as UID 101 with all capabilities dropped, no network/listener |
| `docs-visual-attempt-5.json` | Passed, limited scope | Preview permissions, typed values, pagination and limits readable at authenticated `/docs` desktop page |

| `backend-unit-attempt-10.xml.gz`, `backend-result-attempt-10.json`, `scorecard-attempt-8.txt`, `user-data-boundary-attempt-7.txt` | 11,821 passed; zero failures/errors/skips; scorecard and boundary passed | Run began before stream-cleanup correction; two changed paths explicitly recorded. The final corrected source needs the next complete backend gate |
| `evidence-redaction-attempt-6.json`, `configured-secret-scan-attempt-5.json` | Seven configured fixture values removed from public JUnit; zero remaining exact configured-secret matches in 687 owned files | Restricted original retained; compressed evidence hashes recorded separately |
| `query-schema-grammar-drift-attempt-2.json` | Passed | Owned grammar files staged, canonical regeneration byte-identical, no untracked grammar artifacts |

| `frontend-gates-attempt-16.json`, `frontend-coverage-attempt-16-title-tooltip.png` | Lint/build passed; coverage had 1 failure | Existing Studio title locator matched both title and tooltip; exact-title assertion fixed without weakening expected behavior |
| `frontend-gates-attempt-17.json` | Install/lint/build passed; 1,717 tests passed in 202 files | Complete frontend gate before subsequent numeric/source-update additions |
| `source-identity-live-attempt-20-initial.json`, `source-identity-live-attempt-20.json` | Failed, retained; cleanup verified | Fixture output-path shadowing lost the initial nested detail; corrected runner and explicit evidence note retained |
| `source-identity-live-attempt-21.json`, `source-identity-live-attempt-22.json`, `typed-table-dataset-live-attempt-2.json`, `typed-table-dataset-live-attempt-3.json` | Failed, retained; cleanup verified | Real catalog Boolean appeared as tinyint(1), causing an integer preview; diagnostic records types, not data values |
| `dataset-analyzed-types-unit-attempt-1.xml`, `source-identity-live-attempt-23.json`, `typed-table-dataset-live-attempt-4.json`, `null-sql-dataset-live-attempt-4.json` | 224 unit tests passed; live typed authoring/preview passed | Object and SQL inspection now use authoritative analyzer types; 28 fixture, 44 typed table and 45 NULL-first SQL assertions; original grants/attributes preserved |
| `dataset-numeric-unit-attempt-1.xml`, `dataset-numeric-browser-attempt-1.txt` | 309 unit and 48 browser tests passed | Finite float wire contract, exact decimal/int separation, numeric bindings and fixed-offset timestamp support |
| `source-identity-live-attempt-24.json`, `typed-table-dataset-live-attempt-5.json`, `float-engine-boundary-attempt-1.json`, `float-engine-boundary-attempt-2.json` | Failed fixture retained; direct engine boundary observed | Very small unquoted scientific literals became zero in pinned FE; explicit text-to-DOUBLE cast preserves value. Initial diagnostic used unavailable PUBLIC role and was repeated with login default role |
| `source-identity-live-attempt-25.json`, `typed-table-dataset-live-attempt-6.json`, `null-sql-dataset-live-attempt-6.json` | Passed; owned fixture removed | Real FLOAT/DOUBLE API transport retains subnormal/max finite values from explicitly established input; typed literal lowering remains open |
| `dataset-update-unit-attempt-1.xml`, `dataset-update-browser-attempt-1.txt`, `dataset-update-http-attempt-1.xml` | 347 focused unit tests, 50 browser tests and HTTP admission passed | Source edits check document permission before engine I/O, re-inspect outside PG, preserve revision CAS and reject silent semantic-binding removal |
| `source-identity-live-attempt-26.json`, `dataset-repair-live-attempt-1.json`, `sql-dataset-update-live-attempt-1.json` | Passed; original identity and fixture cleanup verified | 56 view repair and 54 SQL edit assertions through normal gateway; replaced-view previews fail, new IDs are explicit, replay/CAS/intent conflicts and original publication preserved |
| `ruff-attempt-31.txt`, `ruff-attempt-32.txt`, `ruff-attempt-33.txt`, `mypy-attempt-31.txt`, `mypy-attempt-32.txt`, `contracts-drift-attempt-12.txt` | Initial style failures corrected; latest lint/types and drift passed | Current numeric and source-update contracts/services |
| `docs-visual-attempt-6.json`, `dataset-update-docs-attempt-1.txt` | Browser docs test and authenticated visual check passed | Numeric preview limits and explicit source repair documented; API availability distinguished from unfinished native editor |

| `backend-gates-attempt-11.json`, `backend-result-attempt-11.json`, `backend-unit-attempt-11.xml.gz` | 11,860 passed; zero failures/errors/skips; scorecard and user-data boundary passed | Final source unchanged throughout run; 66.95% line and 59.70% branch coverage for analyzed schema, float transport, stream cleanup and explicit source edits |
| `evidence-redaction-attempt-7.json`, `configured-secret-scan-attempt-6.json` | Originals retained outside Git; public artifacts redacted immediately; 716 owned files had zero configured-secret matches before full-gate outputs | Compressed hashes recorded; next scan covers final gate artifacts |
| `publication-projection-unit-attempt-1.xml`, `publication-projection-browser-attempt-1.txt`, `pg-attempt-15.xml` | 133 focused unit, 52 browser and 50 real PG17 tests passed | Publication allowlist hides SQL/formulas from owners and viewers; authorized draft editing retains the definition; exact integral float JSON values accepted without lossy coercion |
| `source-identity-live-attempt-27.json`, `dataset-repair-live-attempt-2.json`, `sql-dataset-update-live-attempt-2.json`, `docs-visual-attempt-7.json` | Passed; owned source fixture removed and dataset fixtures soft-deleted | Governed API privacy, source repair and immutable publication regression; updated publication docs inspected in authenticated Chrome |
| `frontend-gates-attempt-18.json`, `frontend-drift-attempt-18.json` | Frozen install, lint, build and 1,722 tests passed | Only ignored test screenshot outputs changed during the run; application source and configuration stayed unchanged. This run precedes semantic dataset additions |
| `semantic-dataset-unit-attempt-1.xml`, `semantic-dataset-browser-attempt-1.txt` | 184 focused unit and 60 browser tests passed | Pinned Semantic View selection, existing compiler, authoritative schema, private-source refusal, typed preview and transport validation |
| `source-identity-live-attempt-28.json`, `semantic-dataset-live-attempt-1.json` | Failed, retained; fixture cleanup passed | Query workload lacked Semantic View metadata read access; migration 51 adds scoped read-only grants/RLS. Fixture and governed rerun required before activation |
| `pg-attempt-16.xml`, `pg-attempt-17.xml` | Initial 116 passes / 2 failures retained; repeated suite passed all 118 | Native isolation, Intelligence and migrator suites; old assertions updated for authorized query reads, while draft/private/version RLS and write refusals remain tested |
| `development-schema-51.json`, `development-readiness-schema-51-attempt-1.txt` | Backup verified; upgrade and readiness passed | Existing table counts and epoch preserved; same checkout restarted, no replacement stack or service identity bypass |
| `source-identity-live-attempt-29.json`, `semantic-dataset-live-attempt-2.json`, `semantic-fixture-finding-attempt-2.json` | Failed, retained; fixture cleanup passed | Fixture omitted explicit dimension kind; shared compiler correctly refused it, fixture corrected |
| `source-identity-live-attempt-30.json`, `semantic-dataset-live-attempt-3.json` | Governed lifecycle passed; cleanup verified | Published model inspection, typed decimal preview, immutable version pin after new model publication, active-role refusal, publication privacy and deprecated-model refusal |
| `semantic-dataset-unit-attempt-2.xml`, `semantic-dataset-unit-attempt-3.xml`, `semantic-client-types-attempt-2.txt`, `contracts-drift-attempt-13.txt` | 53 semantic regressions and 97 dataset/graph tests passed; types and contract drift passed | Shared compiler regression, invalid/corrupt models and pinned local-source graph. Initial standalone TypeScript CLI invocation failed; project-configured check passed |
| `configured-secret-scan-attempt-7.json` | Passed; 769 owned files, zero configured-secret matches | Includes decompressed backend gate artifacts; exact configured-secret scan is not a complete secret-detection audit |
| `docs-visual-attempt-8.json`, `semantic-dataset-docs-attempt-1.txt` | Authenticated docs visual check and 13 docs tests passed | Semantic version pinning, permission refusal and current editor limits documented |
| `publication-query-history-review-1.json`, `publication-query-history-review-2.json` | Live reproduction retained; Nova history boundary corrected and verified | Source-free provenance preserves audit identity and correlation. Engine log/dump/process-list boundaries remain open |


| `backend-gates-attempt-12.json`, `backend-unit-attempt-12.xml.gz`, `backend-drift-attempt-12.json` | 11,886 unit tests passed, no skips; scorecard and user-data boundary passed; no source drift | Covers publication projection and semantic datasets before the subsequent query-history privacy correction |
| `frontend-gates-attempt-19.json`, `frontend-toolchain-correction-1.json` | 1,730 browser/Node tests passed; lint/build passed | Attempts 18–19 used Node 22.12.0/pnpm 10.13.1. They are not CI-equivalent dependency validation; attempt 20 uses Node 22.22.0/pnpm 11.8.0 explicitly |
| `source-identity-live-attempt-31.json`, `source-identity-live-attempt-32.json`, `sql-dataset-history-live-attempt-2.json` | First attempt failed fixture connection setup; next attempt reproduced source SQL in caller history | No shared data reset; owned fixtures cleaned and QA attributes/grants unchanged |
| `query-privacy-unit-attempt-1.xml`, `mypy-attempt-37.txt`, `query-privacy-ruff-attempt-2.txt` | 127 focused tests, full-application mypy and changed-file Ruff passed | Task-local provenance, audit correlation/identity, source-free history/result metadata, profile suppression, borrowed-session refusal and semantic regressions. Ruff attempt 1 retained its formatting failures |
| `source-identity-live-attempt-33.json`, `sql-dataset-history-live-attempt-3.json`, `semantic-dataset-live-attempt-4.json` | Governed rerun passed; cleanup verified | SQL source absent from caller history while history remains populated. Separate engine audit/dump/process-list privacy acceptance remains open |
| `source-identity-live-attempt-34.json`, `semantic-dataset-live-attempt-5.json` | Failed fixture assertion setup; retained and cleaned | Stronger history fixture accidentally gated SQL-only AST assertions on the history marker for semantic datasets. Production query code unchanged; fixture correction and rerun required |
| `docs-visual-attempt-9.json`, `docs-visual-attempt-10.json` | Authenticated docs checks passed | Publication-response scope and new query-history explanation visually inspected; screenshot evidence observed in conversation |
| `virtual-table-install-attempt-1.txt`, `virtual-table-install-attempt-2.txt` | Wrong pnpm store layout refused first install; pinned toolchain install passed | Adds MIT TanStack Virtual 3.14.26 with exact version. No global tooling configuration changed |
| `virtual-table-browser-attempt-1.txt`, `virtual-table-browser-attempt-2.txt`, `virtual-table-browser-attempt-3.txt` | Initial missing stylesheet fixture failed; corrected 5 table tests and 13 docs tests passed | Exact numeric text, safe text rendering, two-axis virtualization at 200 rows/512 columns, keyboard navigation, security-context remount and empty result; both themes at 320 px |


| `backend-gates-attempt-13.json`, `backend-unit-summary-attempt-13.json`, `backend-drift-attempt-13.json` | 11,900 unit tests passed, no skips; changed-file Ruff, scorecard and user-data boundary passed; no source drift | Application code includes the query-history privacy correction. Raw logs and coverage are restricted local evidence with SHA-256 recorded in the gate report. Overall line coverage 66.99%, branch coverage 59.76%; this does not satisfy feature-specific coverage by itself |
| `source-identity-live-attempt-35.json`, `sql-dataset-history-live-attempt-5.json`, `semantic-dataset-live-attempt-6.json` | Passed; fixture cleanup and QA grants/attributes verified | Corrected fixture verifies resource ID, caller, revision and successful query-history status without generated SQL for SQL and semantic datasets |
| `frontend-gates-attempt-20.json`, `frontend-gates-attempt-21.json` | Install, Chromium setup, lint and build passed; coverage remains failed | Attempt 20 browser connection closed before completing the suite; attempt 21 completed with 1,733 passes and 2 failures in existing Agents/Notebook tests. Both source snapshots stayed unchanged; focused diagnosis required |
| `tanstack-license-acceptance-1.json`, `frontend-license-attempt-3.json`, `frontend-audit-attempt-5.json` | MIT feature/license check passed; dependency audit has 0 critical, 0 high, 4 moderate and 2 low advisories | NDV-OSS-005 passes its license/static scope. Vulnerability findings remain tracked for WP16; license inventory does not prove distribution notices are complete |


| `frontend-regression-diagnostic-attempt-1.txt`, `frontend-coverage-attempt-22.txt`, `frontend-drift-attempt-22.json` | Focused 47 tests passed, then all 1,735 tests/203 files passed; no source drift | Same source as successful attempt-20 install/lint/build. Earlier browser disconnect and two timing failures retained. Root cause of intermittent timing remains unconfirmed |
| `frontend-sbom-virtual-table.cdx.json`, `frontend-sbom-virtual-table-license-evidence.json` | 738 components; no missing declared licenses | Original generated SBOM retained compressed; five missing entries filled from exact installed TypeScript ESLint package metadata with hashes |
| `configured-secret-scan-attempt-8.json` | 858 owned files checked; zero exact configured-secret matches | Includes decompressed gzip and fixture credentials/PG passwords of at least eight characters. This is not a comprehensive secret-detection audit |


| `preview-panel-browser-attempt-1.txt`, `preview-panel-types-attempt-2.txt`, `preview-panel-lint-attempt-1.txt` | 10 browser tests, project types and focused lint passed | Real native-client preview flow, server pagination, explicit query action, safe refusals, empty result and browser abort on role change/unmount. Initial type check caught optional next-offset handling and was corrected; product editor integration remains open |
| `dashboard-datasets-unit-attempt-1.xml`, `dashboard-datasets-browser-attempt-1.txt`, `dashboard-datasets-ruff-attempt-2.txt` | 118 focused backend tests, 78 browser tests and changed-file lint passed | Pinned dashboard data registry, bounded graph closure and backward-compatible empty serialization; first lint attempt retained formatting failure |
| `pg-attempt-18-setup.json`, `pg-attempt-19.json` | Setup guard refused URI-only DSN parsing before any DB operation; corrected run passed 121 tests with no skips | Existing isolated PG17 fixture; native/Intelligence/migration suites include direct dataset references, hidden/cross-workspace refusal and retained-publication deletion protection. No new schema migration or dev reset |

The shared table is implementation groundwork; it is not mounted in either native
editor yet. It does not establish completed chart tables, server pagination or a
Data tab. No additional QA row is promoted on this component-only evidence.

Evidence applies to the source at each attempt; later changes require affected
checks again. Full backend attempt 8 passed after the retained attempt-7 fixture
failure. Frontend attempt 17 passed before numeric/source-update additions; those additions have
focused browser/type checks. Frontend attempt 18 subsequently covered numeric,
source-update and publication projection changes. Backend attempt 11 covered
the stream-refusal correction but preceded the publication projection and later
semantic additions; it is not full-suite evidence for those later changes.
Published dataset SQL projection has focused, PG and governed API evidence.
Remaining governed integration, engine-side sensitive-value redaction, semantic
chart planning, security E2E, performance and native-editor visual QA remain pending.
NDV-OSS-003 is `PASS` for the pinned GridStack real-browser compatibility gate.
NDV-OSS-005 is `PASS` for exact-version MIT Table/Virtual licensing and feature sources.
NDV-DATA-015 is `PASS` for inactive-role schema refusal through the normal gateway.
NDV-CHART-001, NDV-CHART-004, NDV-GRID-012, NDV-FILTER-002, NDV-FILTER-004,
NDV-INTER-001 and NDV-CACHE-009 pass on 16 November product evidence.
NDV-DATA-001, NDV-DATA-002 and NDV-DATA-009 also have complete governed API
evidence. NDV-DATA-007 and NDV-DATA-008 pass on governed parameter evidence from
16 November 2026. The remaining 239 scenarios are `NOT_RUN` until their entire
assertion has evidence. A passing narrow unit test does not satisfy a broader QA scenario.

## Test topology and protected state

The user authorized one isolated PG17 fixture without additional StarRocks or
changes to the development stack. It runs as Compose project
`nova-native-dataviz-test`, bound to loopback port 55433, using the repository's
pinned PostgreSQL 17 fixture with TLS. Credentials/configuration are outside Git.
The fixture reports PostgreSQL 17.11. Schema reset tests target only that fixture.
After a fixture process recovery, the same fixture's resource bounds were
adjusted from one CPU/512 MiB to two CPUs/1 GiB for the second full regression.
There was no OOM-kill event; the crash's root cause is not established. Both
topologies and failed attempts are retained in `pg-fixture-recovery.json`.

The development database was backed up and upgraded through the separate
migrator to schemas 48, 49 and 50, retaining its epoch and existing table counts.
Schema 49 checked 193 existing tables. After the schema-50 upgrade, the original
checkout's processes were restarted and all runtime roles reported ready.
Migrations 48–51 are applied and immutable. These additive stores and HTTP
routes are not a native Dataviz product cutover.

Authenticated visual `/docs` verification passed through native Chrome app
control after the user completed sign-in. The three changed pages, their new
paragraphs, section navigation and native-document link were inspected at
1920 × 1080 in the current dark theme. Earlier extension timeouts and the
sign-in redirect are retained as unsuccessful attempts. This docs check does
not establish native editor, mobile or light-theme acceptance.

## Superset decommission (2026-11-16)

The user directed removal of every Superset integration and its image ahead of
WP14 conversion and WP16 release QA. That order is a recorded user decision, not
a passed dependency.

- Removed: the `superset/` image build, Compose services and volume, the `/bi`
  gateway route, `backend/app/modules/superset/`, the Superset Dataviz tools,
  BI metadata backup, Semantic View BI sync, the Studio artifact editor routes,
  the frontend BI workspace and Legacy BI mode, the CI Superset job and the
  Superset-only tests.
- Kept: delegated task sessions moved to `task_orchestration/delegation.py`
  (same Redis prefix); the data-token proxy login now serves notebook and task
  tokens only; historical migrations and shared legacy dashboards stay readable.
- Replaced: **Add to Dataviz** now checks and publishes through
  `viz/studio_publish.py` as the caller at the same HTTP paths.
- Data: the development Superset metadata database was dumped to a private
  location outside Git before its containers, image and volume were deleted.
  The 7 dashboards, 52 charts and 22 datasets in the earlier inventory were
  not converted; rebuilding any of them is manual.
- Evidence: backend gate attempt 18 (`backend-gates-attempt-18.json`: mypy,
  changed-file Ruff, 11,834 unit tests, scorecard 54/54, boundary); PG17 fixture
  `test_metadata_agents`, `test_metadata_assistant_policy_bi` and
  `test_metadata_viz` (190 passed); frontend lint, build and the full browser
  suite (the last full run had one notebook timing failure under load that
  passed 3/3 in isolation, with no notebook source changed); and the in-app walkthrough in `native-dashboard-ui-qa-attempt-2.json`,
  which found and fixed 12 defects, including an empty dashboard list in
  **Add to Dataviz**. Engine-backed integration suites run in CI on the pull
  request.

## Live Neva, governed viewers and new features (2026-11-16, later)

- **Neva live:** the first live question ("which region leads, by how much")
  failed on a date level sent for a text field, then on `sum(revenue)` as a
  sort name, then on a garbled calculation label. Fixes: native tool refusals
  are recoverable within the turn; a date level on a non-date field is ignored;
  `agg(field)` sort names resolve; `compute_metrics` stays available on an open
  dashboard; a required calculation that still fails leaves an answer from the
  verified rows instead of an error. Final run: Jakarta 979,755,000 leads
  Surabaya 803,480,000 by 176,275,000 (verified calculation). Tool identifiers
  no longer appear in step labels.
- **Governed viewers:** a steward-published dataset over `analytics.sales`
  failed for every non-admin viewer. Two causes: Ranger hid the
  `information_schema` reflection views from non-admin roles (only the removed
  BI author list had them), and a row filter made the analyzer report columns
  as nullable, so re-verification reported a stale schema. Fixes: the managed
  reflection policy now covers Ranger's `public` group (bootstrap and compiler),
  and re-verification keeps the saved nullability when names, order and types
  match. Evidence: `row-filters-live-attempt-1.json` to `-8.json` (attempts 1–7
  retained as failures; 8 passes 25 checks); source-identity attempt 20 and
  parameter/aggregate attempt 8 pass.
- **Add to Dataviz:** the same answer is no longer added twice to a dashboard,
  a different chart from it reuses the dashboard's dataset, and charts get a
  short title from their fields when the answer's title is a question.
- **Export, drill-down, pivot, conditional formatting:** see WP05, WP08 and WP13
  above; user docs updated in `/docs` (Native dashboards, Add to Dataviz,
  Neva in Dataviz).
- **Layout fix:** returning from the one-column narrow view restores each
  widget's saved width (previously every widget stayed one column wide).
- **Gates on the delivered source:** backend gate attempt 20 (mypy, changed-file
  Ruff, 11,862 unit tests, scorecard 54/54, boundary); `tests/eval` 353 passed;
  PG17 `test_metadata_viz` + `test_metadata_agents` 149 passed; frontend lint
  (0 errors), typecheck, build and the full browser suite: 1,800 of 1,801 passed.
  The one failure is the notebook Markdown-cell test timing out at 15 s under
  full-suite load; it passes alone (36/36, three times) and no notebook source
  changed. Engine-backed suites run in CI on the pull request.

## Release QA round 3 (2026-11-16)

Of the 158 open scenarios, 69 now pass and 89 stay `NOT_RUN`. The matrix
counts are 148 PASS, 89 NOT_RUN and 17 OUT_OF_SCOPE.

- **New evidence:**
  - PG17 refusals and races: `backend/tests/integration/test_metadata_viz_qa.py`.
  - A live governed run as alice, bob and the marketing and finance roles:
    `qa-live-attempt-6.json`. Attempts 1 to 5 are kept as failures; they were
    script bugs and missing dataset grants.
  - Unit and trajectory tests: `backend/tests/unit/test_native_viz_qa_*.py` and
    `backend/tests/eval/test_native_dataviz_qa_trajectories.py`.
  - Browser tests: `frontend/src/features/native-dataviz/*/qa-*.test.ts(x)`.
  - A gzipped bundle budget, `pnpm check:bundle`, which runs in CI after the
    build.
- **Defects found and fixed, each with a test that fails without the fix:**
  - A saved gap between widgets closed up on reload. GridStack now keeps the
    saved x and y.
  - An existing widget could not be selected from the keyboard. Widgets now
    take focus in Edit.
  - Focus was lost after closing the Filters window. It now returns to the
    button that opened it.
  - A missing month disappeared from a time axis. It is now a gap, or zero
    when the chart asks for it.
  - Series colours moved when a filter removed a series. Colours now follow
    the series name.
  - A KPI with no data showed only a dash. It now also says **No data for the
    current filters**.
  - A renamed saved column returned 422. It now returns `SCHEMA_STALE` 409.
  - Derived semantic ratios had no zero guard. Every divisor is now wrapped in
    `NULLIF`.
  - Neva's arrange was refused when anyone changed any other widget. Now only a
    concurrent change to a widget that Neva moves conflicts.
  - Also added: a **Refresh data** button that runs every chart again.
- **Still open:**
  - STORE-003, a defect: scheduled-export retries are not idempotent.
  - Missing features: CHART-016 (server pagination), AGENT-007 and AGENT-009
    (approval previews that name widgets) and FILTER-008 (a null option for
    true/false filters).
  - Partly done: INTER-003 (no explanation is shown), AGENT-021 (a
    direct-answer turn can still claim a change), METRIC-015 (engine week check)
    and PERF-003 and CACHE-011 (load runs).
  - OSS-008: pip-audit reports 93 advisories in 11 Python packages
    (`backend-vulnerability-audit-1.json`). Upgrading them is a separate change.
- **Gates:**
  - Backend gate attempt 24: mypy, changed-file Ruff, 12,054 unit tests,
    scorecard and boundary passed. `tests/eval`: 359 passed.
  - PG17 suite: 1,059 passed and 8 skipped. The fixture's server crashed
    (exit code 2) while the frontend suite ran alongside it, which caused 25
    connection errors in `test_metadata_workspaces.py`. The leftover fixture
    state was removed and the file was rerun alone: 42 passed.
  - Frontend: lint (0 errors), typecheck, build and the bundle budget passed.
    The full suite passed 1,882 of 1,883. The failure was a long drill-down
    test that timed out under load; it now has a 30 s limit and passes.
  - Record: `release-qa-attempt-3.json`.
- **CI fix after the round-3 push:** CI's PG17 job failed
  `test_business_policy_revisions_have_one_winner`. One of three racing policy
  saves got the insert trigger's "revision follows" check violation (23514)
  instead of a conflict. That happens when its trigger runs after another save
  commits. The repository now reports that specific trigger refusal as a
  revision conflict. A deterministic PG17 test fails without the fix. The file
  passes 42 tests and backend gate attempt 25 passes.

## Release QA round 4 (2026-11-16)

Eleven more scenarios pass: 159 PASS, 78 NOT_RUN and 17 OUT_OF_SCOPE. The
record is `release-qa-attempt-4.json`.

- **Built:**
  - Server-side table pages: a Table shows **Rows X–Y** with previous and next
    arrows, up to a 10,000-row offset (CHART-016).
  - A **No value** choice for True / false filters (FILTER-008).
  - A hover explanation on charts that cannot filter others (INTER-003).
  - Neva's approval text names the charts a filter affects and each widget a
    layout change moves, resizes or removes (AGENT-007, AGENT-009).
  - On a dashboard turn, Neva's answer cannot claim a change or publication
    that it did not save (AGENT-021).
- **Defects fixed:**
  - Scheduled-export retries now save one workbook per occurrence, verified
    by a receipt inside the file (STORE-003).
  - Switching dashboard pages aborted queries the server kept running, which
    caused 429 storms. Sent queries now finish (PERF-003).
- **Live evidence on the development stack:**
  - `qa-live-attempt-8.json`: ISO weeks across three new years. Attempt 7 is
    kept as a failure: a literal-only SQL source is refused.
  - `load-live-attempt-3.json`: 262,144 keys paged against the SQL oracle,
    and a burst of 40 queries (4 answered, 36 refused with 429, no errors).
    Attempts 1 and 2 are kept as failures: the oracle returns at most 500 rows.
  - `perf-live-attempt-1.json`: a 50-widget dashboard in the browser.
- **Dependencies (`dependency-audit-2.json`):**
  - Seven Python packages were upgraded, and python-jose was replaced by PyJWT.
  - KaTeX and esbuild were upgraded.
  - The frontend audit is clean.
  - asyncmy has no fixed release. Its advisory is mitigated by a driver guard
    that refuses mapping parameters.
- **Environment:** the StarRocks FE container exited again (code 255) during
  this work and was restarted with its data. Local scipy is 1.17.1, because the
  locked 1.15.3 wheel used earlier was no longer present.
- **Gates (round 4):**
  - Backend gate attempt 26 failed: 4 MySQL driver guard tests had no event
    loop in the full suite. Attempt 27 passed: 12,110 unit tests, mypy,
    changed-file Ruff, scorecard and boundary.
  - `tests/eval`: 366 passed. PG17: 1,085 passed and 8 skipped.
  - Frontend: lint, build and bundle budget passed. No full browser run was
    clean:
    - Run 31 stopped on a test whose page reached the network; fixed.
    - Runs 32 to 34 each had one timing failure in an unchanged test while the
      host was heavily loaded by another session's build. Each passed alone.
  - Record: `release-qa-attempt-4.json`.
- **StarRocks compatibility audit (started 2026-11-16):** see
  `docs/architecture/starrocks-compat/PLAN.md`.

## Release QA round 5 (2026-11-17)

Every CRITICAL scenario now passes or is out of scope: 172 PASS, 65 NOT_RUN
(all HIGH) and 17 OUT_OF_SCOPE. Rows changed in this round: CACHE-001 to 003,
OPS-001, OPS-002, ACL-012, STORE-002, STORE-004, GRID-001, METRIC-002,
AGENT-001, OSS-001 and OSS-002.

- **Defects fixed:**
  - Downloads reused chart rows cached before a Ranger change. Downloads now
    always re-run the governed query; on screen, rows are kept for at most
    30 seconds (CACHE-001 to 003).
  - Presence kept a viewer's access for 30 seconds after sharing changed. It
    is now 5 seconds, and changing grants or deleting a dashboard clears it at
    once (ACL-012).
  - A semantic ratio metric (for example a margin) was summed at a coarser
    chart grain, giving wrong totals. The query is now compiled again at the
    chart's grain, or refused with a plain message when that is not possible;
    only declared-additive SUM/COUNT metrics are re-totalled (METRIC-002). The
    control run without the fix (`metric-ratio-control-without-fix.json`)
    shows East 0.85 and total 1.19 instead of 0.32 and 0.19.
  - Neva's chart builder truncated large results differently from the manual
    builder; both now match (AGENT-001).
  - Workspace files were served without an integrity check. Migration 0059
    records a SHA-256 for every saved file and version, and reads refuse a
    mismatch (STORE-004). Development is at schema 59 (`development-schema-59.json`).
  - When a dashboard save's reply was lost, the editor took the change back
    even if it had been saved, so the user could add a widget twice. The editor
    now checks the operation's receipt (OPS-001).
- **Built:** drag a Chart, KPI, Table or Text from the toolbar onto a canvas
  cell, with a placement preview (GRID-001).
- **New blocking CI gates:** `backend/scripts/check_licenses.py` and
  `pnpm check:licenses` fail on a non-allowlisted license anywhere in the
  installed trees (135 Python distributions and 646 npm packages, 0 findings).
  They also fail on known commercial or open-core packages and on license-key
  APIs (OSS-001, OSS-002). This is an engineering gate, not a legal notice
  sign-off.
- **Live evidence:** `policy-change-live-attempt-2.json` (Ranger filter 1.1 s,
  mask 5.1 s, revoke 4.3 s to take effect). `metric-ratio-live-attempt-2.json`
  passes; attempt 1 is kept as a script failure on null cells.
- **Fault evidence:** `ops-001-pg-fault-attempt-1.txt`. Client connections were
  terminated mid-save, and one commit reply was dropped on the disposable PG17
  fixture. The PG process itself was not restarted.
- **Still open:** 65 HIGH scenarios, WP14 conversion, server-side PDF/image
  export, websocket replay, and the open items listed per work package above.
- **Gates (round 5, local):**
  - Backend unit 12,558 passed, plus 15 license-gate tests run separately.
    Changed-file Ruff and full mypy (767 files) pass.
  - PG17 fixture: workspace, viz-export and migrator suites, 92 passed. The
    first run had 2 failures in new test code (a wrong argument and a write
    guard); both were fixed. Viz and viz-QA suites: 92 passed.
  - Frontend: lint (0 errors), build, bundle budget, license gate, and
    coverage with 222 files and 1,935 tests, all passed.
  - CI run 44012358710, for the previous commit, failed Backend static on
    Ruff in `recheck_live.py`, which this batch fixes. Every other job passed,
    including integration L3.
- **First CI run of the license gate (run 44019145194):** it failed on
  `nvidia-nccl-cu12` (NVIDIA proprietary license). On x86_64 Linux, xgboost
  pulls it in through `flaml[automl]`; macOS does not install it. A uv override
  now never installs it. Multi-GPU training is the only use of NCCL, and Nova
  does not run it. CPU xgboost trains without it
  (`license-nccl-exclusion.txt`). This also removes about 340 MB from each
  Linux backend image.
- **Round 5, later:** the bundle now serves third-party notices
  (`/licenses/third-party.md`, with the ECharts and es-toolkit NOTICE files),
  and `pnpm check:bundle` enforces them (OSS-004, OSS-007). REVIS-006 passes
  with the OPS-001 editor fix.
- **Gates (round 5, later):** frontend coverage attempt 1 had 10 timeout
  failures in 5 unchanged files. The host load average was 41 (5-minute) and
  23 (1-minute) from parallel work. The 5 files passed alone (87 tests).
  Attempt 2 was clean: 223 files and 1,946 tests. Lint, build, bundle budget
  with notices, and the license gate passed. CI run 44019145194 (`f5ccacbb`):
  every job passed except Backend static, which stopped at the NCCL finding.
  Ruff and mypy did not run in that job; the next push runs them.
- **Round 5, third batch:**
  - **Defect fixed:** every native and Studio cartesian chart set
    `grid.containLabel`. ECharts 6 ignores it unless the legacy feature is
    registered, and warned on each render. The charts now use the documented
    equivalent (`outerBoundsMode: 'same'`, `outerBoundsContain: 'axisLabel'`).
    The result was checked visually in light and dark with long labels, and
    `check:bundle` now refuses `containLabel`.
  - **New passes:**
    - GRID-009: moving a chart keeps its live ECharts instance and the
      viewer's selection.
    - CACHE-004: ten rapid filter changes with out-of-order answers show only
      the newest result, and queued superseded queries are never sent.
  - **Still partial:** PERF-007. Sent queries finish by design (PERF-003), and
    there is no server cancellation metric.
  - **Gates:** frontend lint, build, bundle and notices, and coverage with
    223 files and 1,948 tests, all passed.
  - **CI run 44023243065 (`6aa4dcf2`):** Backend static passed, including the
    license gate on Linux, Ruff and mypy. Frontend passed.
- **Round 5, fourth batch:**
  - **Built:** keyboard access to chart marks (INTER-011). A chart that can
    filter is a Tab stop with a visible focus ring. The arrow keys move between
    marks, each one is announced, Enter does what a click does, and Escape
    leaves the mark.
  - **Defects fixed:**
    - Tooltips were white in dark mode; they now use the theme's surface and
      text.
    - A chosen filter value that is no longer in the data vanished from the
      list. It now stays listed, ticked and marked **Not in the data now**
      (FILTER-016). The test fails against the old code.
    - The filter search field drew its own box in dark mode; it no longer does.
  - **New passes:** A11Y-004 (theme switch with a chart drawn; contrast:
    light 5.20 and 14.63, dark 7.53 and 17.09) and FILTER-018 (200-value cap,
    note, server search).
  - **Checks:** visual checks in light and dark. `/docs` dataviz 08 updated.
    Frontend: 224 files and 1,952 tests, plus lint, build and bundle checks,
    all passed.
