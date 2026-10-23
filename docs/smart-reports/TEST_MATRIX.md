# Verification matrix

All evidence below is local. No GitHub Actions result is claimed. STATUS.md owns
the current delivery decision; a passing focused check does not replace the gates.

| Requirement | Test/evidence | Boundary |
| --- | --- | --- |
| BI-01 | test_smart_reports.py; browser Jakarta → Bandung → server Ask evidence | Required filters fail closed; optional incompatibility is visible |
| BI-02 | interaction reducer and chart-data tests; keyboard selection controls | No model call on selection |
| BI-03 | details_templates unit; Ranger row-detail acceptance | Published grain keys, scoped rows, forged field/session refusal |
| BI-04 | test_smart_report_engine.py, 11,037 rows | Ratio-of-sums, CASE, rank, moving/running windows, null/zero and exact large decimal |
| BI-05 | calculation/calendar tests; configured semantic timezone suite; independent fiscal SQL | Leap comparison clamp, DST durations, partial periods and fiscal/week starts |
| BI-06 | pivot unit and independent engine subtotal checks | Full-input distinct subtotal; paging does not redefine totals |
| BI-07 | renderer/chart-data/advanced-chart tests; real Chromium snapshot inspection | Synthetic chart smoke is separate from governed query acceptance |
| BI-08 | browser light/dark at 320/390/768/1280; 200% equivalent reflow | Keyboard, container bounds, multi-page and all-page export |
| BI-09 | test_smart_report_ranger.py; lifecycle and security tests | Two users sharing a role, active-role grants, RLS, revocation, handoff theft |
| BI-10 | browser PDF/XLSX/PPTX downloads; real delegated scheduled handler | Current authorization at run/download; deterministic delivery/cooldown; private snapshots |
| BI-11 | eight-widget real Ranger timing; session/cancel tests | In-batch dedupe; no unverifiable cross-request result cache |
| BI-12 | live-model Reports UI; stream test; insight unit tests | Actual provider planning distinguished from scripted streaming; descriptive insight evidence |
| UX-R01 | canonical and copied route, reload and independent session | One Studio shell |
| UX-R02 | chat/full shared session; expired/replay/role-switch unit and integration | Single-use handoff, URL removal |
| UX-R03 | test_smart_report_stream.py; context/security trajectories | Reference-only persisted block; server-resolved Bandung data |
| UX-R04 | editor preview/apply/publish UI; stale publish and reviewed-draft API | Published history immutable until explicit publish |
| GOV-01 | boundary script, patched-FE report/Studio suites, audit/secret review | Ordinary test engine is not Ranger evidence |
| Migration | 103-dashboard fixtures and actual one-source scan/repeat | Legacy source checksum unchanged; mapping placeholder cannot execute |

## Focused evidence

- Report unit suite: 72 passed at the latest focused checkpoint.
- Independent report engine suite: 7 passed on nova-smart-unlimited. It caught
  DOUBLE promotion from oversized decimal literals; the corrected compiler matches
  DECIMAL golden SQL, including 9007199254740993.12.
- Patched-FE report API suite: four functional cases passed (ACL/handoff,
  editor/publisher, scheduled export/template delivery, row detail). The final
  combined rerun is recorded in STATUS.md.
- Eight-widget timing: 20 renders on an authenticated, warm local Ranger fixture;
  latest render p95 2.250 s and filter p95 2.372 s. These are API measurements on small
  governed data with in-batch dedupe, not a multi-tenant production capacity claim.
- Real provider Reports UI: one successful run with deepseek-v4-1-flash:free,
  creating a private draft and rendering Jakarta 1980.00. Earlier attempts were
  safely rejected because report filter IDs were incorrectly supplied as semantic
  named filters. The prompt/schema now make that distinction explicit.
- Browser journey: actual governed HTTP, persisted chat and Chromium. Verifies
  Jakarta → full → Bandung → Ask → chat → reload; independent link defaults;
  preview/apply/publish; all-page downloads; theme/width screenshots. Intelligence
  UI fixture uses server-validated chart/table over real authorized query rows.
- Scripted provider stream: validates shared planner/tool consent, SSE, persisted
  replay and handoff. This is not evidence of autonomous model reliability.

## Repository gates

The final invocation/results are maintained in STATUS.md. Commands follow
.github/workflows/ci.yml and scoped AGENTS.md. Required: backend unit coverage,
changed-file Ruff, full-app mypy, scorecard/affected trajectories, boundary script,
seeded integration, patched-FE/Ranger; frontend ui:check/lint/build/coverage and UI.

Seeded integration reuses nova-smart-unlimited (39030/38030/39408, Redis 36379,
Minio 39000). Patched acceptance reuses docker (29030, Ranger 6080). Fixture reuse
is asserted before tests run. No new engine, volume reset or auth bypass is used.
The local macOS SciPy binary cannot load, so broad backend suites run in a removable
Linux helper. Host browser acceptance still uses the real governed services.

Optional live-provider/Ranger cases skipped in the ordinary engine suite are listed
separately; none is counted as passed there. Live-model and governed cases are
explicitly run with their own prerequisites. V8 coverage currently warns when
trying to parse generated JSON schema imports as JavaScript; application tests
and coverage retain their normal gates.

## Upstream fixture compatibility

The fetched main branch requires Ranger projection and an explicit default role.
Seeded fixtures now supply those contracts: migration and SQL role activation,
Semantic View probes, stage policy checks, Studio resume and Autopilot sessions.
The semantic/stage revocation checks remove the Ranger policy while retaining the
native grant, so a native grant cannot conceal an authorization failure. The pipe
fixture verifies the current refusal to create/resume a background pipe without a
preserved identity, then tests metadata operations on its own legacy native pipe.
These ordinary-engine cases do not establish patched-FE row filtering or masking.
The six previously failing fixture groups passed together: 15 tests in 41.75 s.

The final ordinary-engine run passed 224 cases in 411.68 s. Its 24 skips include
13 governed report/Studio/browser/provider cases run separately, plus custom-tool
live opt-ins, the older Intelligence Ranger fixture, engine-disabled UDF/native task
round-trip, cross-cluster migration, frontend Ranger-admin fixtures, shared-data
engine cases and destructive stream fault-injection prerequisites. Thirty items
were deselected by the engine marker and the separately run long Intelligence case.
Skipped or deselected cases are not included in the pass count.

Final frontend gate after the history-readiness repair: UI validator drift check,
lint, production build and all 1,416 tests in 167 files passed. Coverage was 52.99%
statements, 52.59% branches, 47.88% functions and 54.16% lines. Lint reported 21
warnings and zero errors. The delayed-history regression failed before the guard
and passed afterward, together with the five existing Mission routing cases.
Final prompt/stream plus expanded report browser acceptance: 2 passed in 193.60 s.

Export burst hardening uses the existing query-admission owner with a positive
per-process source budget (default two), while preserving the global query cap.
Focused coverage: 20 admission/export tests passed, including cancellation,
source fairness and the case where the global query cap is unlimited. Real
patched-FE HTTP acceptance also passed: occupied capacity returns 429 and no
manifest; retrying the same operation produces an authorized PDF. These are
bounded-capacity correctness checks, not a production throughput benchmark.

After export-admission changes, the full backend unit/coverage suite passed again:
7,584 tests, 61% coverage, 401.01 s. The current affected trajectory set passed
54 tests in 10.30 s (published sources, chart provenance, active-role/context and
resource boundaries, parallel reads, Intelligent UI composition, semantic guards,
external-content evidence, learning work, numeric evidence and clarification).
The agent scorecard remains 48/48 scenarios and 165/165 checks. Full-app mypy,
changed-file Ruff (109 Python files) and user-data boundary checks passed.

## Hosted CI prerequisite

No GitHub Actions run is claimed. The current workflow triggers on main pushes
and pull requests, and its integration setup provisions the stock test Compose
services only. After the upstream Ranger-only change, those checks also need an
initialized Ranger control plane and its credentials. The local runs above supply
that prerequisite from the existing governed `docker` project while retaining the
separate disposable data engine. Hosted Ranger provisioning remains an upstream
CI-infrastructure prerequisite; local success must not be presented as a verified
hosted CI result.

Final complete seeded-engine run after the admission change: 225 passed, 24
prerequisite/optional skips, 29 non-engine deselections, 7 warnings, 1,378.50 s.
This invocation includes the long restricted-identity Intelligence scenario.
Final local image `nova-smart-report-backend:local` (`afd3bc8fee24`) preserves the
previously verified locked runtime and adds the final compiled frontend plus the
three changed Python files. All 567 packaged Python source hashes match the tested
checkout. A network-disabled Chromium smoke rendered three advanced-chart/map
segments and a 116,727-byte PDF; no database fixture or provider was used for that
packaging smoke.
