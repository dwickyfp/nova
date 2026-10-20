# Intelligent UI v2 acceptance record

Implementation and applicable local acceptance gates passed on 20–21 October 2026 (Asia/Jakarta). Results below include every failed attempt and its resolution. These are local results; no GitHub Actions run or production deployment is claimed.

Baseline: `main` / `d068264f`. Branch: `feat/intelligent-studio-harness-v2`. Local checks below are not GitHub Actions results. Research and contracts: [architecture document](../../architecture/studio-intelligent-ui-v2.md).

## Gate ledger

| Gate | Observed result | Status |
| --- | --- | --- |
| Baseline synchronization | Fetched `origin/main`; zero missing commits at final review | Fetch repeated before commit and push |
| Backend full unit coverage | 7,658 passed, 61% aggregate coverage including public-media transport tests (450.44 s) | Passed locally |
| Backend eval trajectories | 338 passed including simulation repair and coordinated recovery fixtures (44.59 s) | Passed on final application source |
| Harness scorecard | 48/48 scenarios; 165/165 checks after coordination and conditional UI changes | Passed locally |
| Full application mypy | Passed on final application source; two existing annotation notes | Passed locally |
| Changed-file Ruff | 46 changed Python files passed | Passed locally |
| Frontend coverage | 155 files / 1,354 tests passed, 53.36% statement coverage including simulation prose and literal-state validation | Passed locally |
| Frontend lint | 0 errors; 20 existing warnings after conditional-action test escape correction | Passed locally |
| Frontend production build | Passed, Node 22.22.3 and pnpm 11.8.0; includes conditional actions and Smart terminal projection | Passed locally |
| Backend production image | Build passed; Node 22.22.3 and Python imports passed; isolated validator returned the expected calculation; local `.env` absent | Passed locally |
| Compose configuration | Both engine and test Compose interpolation validated without printing configuration | Passed locally |
| Frozen dependency install | Passed with pnpm 11.8.0 | Passed for current lockfile |
| Catalog/bundle drift | Passed | Repeat if catalog changes |
| Production browser fixture | 20 cases passed: light/dark, 1440/768/320 px, 200% reflow, chart filtering/table, state, form validation, keyboard, maps, canvas isolation | Includes network-blocked business map and WebGL fallback |
| Patched FE Ranger | Proxy roles, per-principal row filters, masks, role-switch transaction guard, root guard, agent tool all passed | Passed locally |
| Geospatial public SQL path | 61 checks passed, 15 function signatures; distance/radius/polygon/data transport; fixture cleanup verified | Passed locally |
| Seeded full integration | Final run: 219 passed, 15 skipped, 30 deselected, 1,526.98 s. Includes custom SQL, root/child cancellation race, UI migration/state, scoped metadata, proxy, recovery and worker behavior | Passed locally; skips enumerated below |
| Validator overhead | Initial median 159.8 ms / p95 204.5 ms; loaded-host rerun 266.0 / 482.4 ms; latest 62.1 / 72.9 ms. Shared-loop and validator benchmarks: 12 passed in the final offline command | Host load differs; no speedup claim; engine measurement recorded separately |
| Real query-tool overhead | 20 read-only engine round trips: median 205.1 ms / p95 378.2 ms; one benchmark passed | Measured on the isolated native-RBAC stack |
| Live provider/browser acceptance | See scenario ledger; production replay and live provider drill-down also passed | Passed locally |

The 15 integration skips were explicit environment/capability gates: one opt-in real-provider custom-SQL probe; seven isolated patched-FE/Ranger cases; two unsupported native-engine features (UDF and CREATE TASK); one cross-cluster migration case; two shared-data-engine cases; and two disposable-FE publication/label-retention cases. The native integration run does not substitute for governed acceptance: the separate patched-FE Ranger proxy/agent-tool checks, 61 geospatial public-SQL assertions, and live configured-provider UI runs passed. The 30 deselected tests do not carry the `engine` marker. No skipped case is counted as a pass.

Selected new backend module line/branch coverage: offline experiments 93.45% / 82.26%; run coordination 85.42% / 75%; UI contract/composition 89.71% / 80.77%; public-media adapters 92.78% / 84%. These are measured coverage values, not a claim of exhaustive correctness.

The Linux unit runner uses the locked dependency versions. Native macOS collection has a SciPy 1.15.3 Mach-O loader failure in unrelated ML suites; dependencies were not upgraded or assertions disabled to conceal it. The engine stack for seeded tests is isolated on ports 39030/36379/39000. Developer engine data is not the target of those fixtures.

## Live scenario ledger

All live turns use Nova's configured provider through its normal authenticated API and UI. No provider keys are exported. QA business visuals use explicitly synthetic SQL fixture results, executed under the signed-in user's role. This is separate from the deterministic browser fixture.

| Scenario | Observations | Current status |
| --- | --- | --- |
| Smart assumption calculator | Three generated calculator results displayed 100 × 2 = 200; editing to 150 × 3 produced 450. One explicit reload retained 450. | Three generations and reactive calculations passed; saved 450 restored on reload |
| Smart/manual toggle | Default Smart; disabling with no previous manual choice reveals selector and blocks Send; re-enabling preserves draft. | Observed live and covered by component tests |
| Verified chart/filter/drill-down | Initial evidence and streaming failures fixed. Live streamed chart filters Partner to 500; recovered chart filters Retail to 800 and dispatches a successful drill-down. | Three streamed generations passed filtering; Retail drill-down returned 800 again at 20:38 |
| Business map/radius | Live provider map: 150,000 m includes Jakarta/Bandung; zero includes Jakarta; maximum includes all three. Zoom and Bandung selection work. | At least three live generations and three final-renderer filter combinations passed; selected-row drill-down returned verified branch values |
| Polygon map | First attempt interrupted by worker exit. Retry rendered the exact SQL polygon, selection and zoom; Jakarta/Bandung inside and Surabaya outside match SQL. | Three generated polygons and three final selection/zoom repetitions passed |
| Public map | Three successful generations rendered Monas and Museum Nasional with working marker popups and persistent names. The 21:43 run survived reload during execution; the 21:45 run also passed. One earlier missing-program failure remains recorded. | Three live generations passed; corrected popup screenshot saved |
| Form follow-up | Live required-field validation passed. Three submitted budgets (1,000,000 / 2,000,000 / 3,000,000) returned reserves 100,000 / 200,000 / 300,000 and correct remainders. | Final guarded follow-ups at 11/12/13 million passed with correct allocations and reactive controls; editing the last total to 14 million survived reload |
| Stop/reconnect/thread switching | Direct reload at 20:34 reattached the same run and completed chart/maps; Stop reached server terminal. A second trial retained the stopped label after reload. A third direct Stop at 23:27 also retained its terminal label after reload. | Three immediate Smart Stop trials at 22:21–22:22 passed reload and durable status checks after the race fix |

## Failure history and fixes

Failures remain recorded even after a subsequent pass:

1. Initial Smart calculator claimed interactive controls but emitted no UI. Root projection retained only legacy artifacts. It now preserves sealed UI and ordered prose.
2. Expanded Smart SSE cursor arithmetic exceeded JavaScript safe integers, dropping adjacent terminal fragments. Cursors now travel as decimal strings and compare using `BigInt`; regression tests use actual timestamp-sized values and replay every terminal boundary.
3. The production fixture initially used an invalid GeoMap root. The contract requires `Col`; the fixture was corrected.
4. Adding GeoMap accidentally grouped public image switch cases with the map case. Production acceptance exposed it; the renderer branch was corrected and a full-renderer image fallback regression added.
5. Full frontend coverage first failed three tests after collapsed activity and async state flush: the disclosure expectation, skill-prefill ordering, and new-chat agent expectation. Behavior and assertions were corrected; the subsequent full run passed.
6. Full eval first failed a stale memory test double that did not accept the current repository evidence arguments. Its signature and recorded values were corrected; 331 eval tests then passed.
7. Direct navigation to an agent was overridden by the newest unrelated conversation. History restoration now selects only that requested agent's history; a regression test covers an explicit agent with no history.
8. Configured SQL tables were not registered as business evidence. The runtime now supplies SQL execution provenance and classifies data versus catalog output. Unmarked custom output and connector claims remain untrusted; regression tests cover both.
9. The first full engine run remained active for over 17 minutes and was interrupted for diagnosis. Async stack inspection showed continuing metadata publication/access/audit work, not a completed suite. The run is recorded as incomplete, not passed.

10. The public workflow sanitizer removed UI provenance scope during SSE projection. Only the two public UI scope labels are now retained; security scope objects remain stripped. New tests cover both business and simulation frames.
11. The dev dependency prebundle bypassed MapLibre class-field transformation, destroying typed buffers. Development now uses the official compiled module; production retains the tested source transform and split chunks. Temporary diagnostic logging was removed.
12. SQL DECIMAL coordinate columns arrive as strings. GeoMap now accepts strictly formatted decimal coordinate columns; empty, boolean, hexadecimal, exponent and comma-formatted values remain invalid. GeoJSON coordinates still require numbers.

13. Concurrent development and engine acceptance overloaded the local StarRocks planners. A worker startup failure stopped the development process and interrupted the polygon turn. Restricted-identity integration failed at different semantic authorization stages under that load. Subsequent engine/provider acceptance runs are serialized.
14. Native full integration collection also hit the pre-existing SciPy Mach-O failure. It is not counted as a run; the full suite is routed to the locked Linux environment.
15. Reload during direct-agent drill-down left the page disconnected from work that continued on the server. Reload now discovers the scoped run and replays its journal without resubmitting. Direct Stop now requests server cancellation and waits for its terminal event. Unit/component coverage includes scope denial, cancellation and GET-only recovery.
16. Recovery token accounting originally restarted measured totals. Checkpoints now retain consumed tokens; missing, malformed and exhausted counters fail closed before another provider call.
17. The combined chart/map repeat at 20:00 reached a provider ReadTimeout after reconnect. It is not a successful acceptance trial.
18. Another metadata planner timeout terminated the worker and interrupted the next calculator submission before acceptance. Only this specific polling timeout now retries with bounded backoff; SQL and access failures remain terminal.
19. Concurrent browser runs failed timing/iframe checks. New active-run discovery also consumed mocked request responses in Mission routing tests. Discovery is now limited to a persisted unanswered user turn. The final serial frontend run passed 1,354 tests; the failed run is retained rather than counted as a pass.

20. GeoMap loading completion closed a disclosure the user had opened. Disclosure now owns its state; a component regression keeps table selection visible after canvas loading.
21. A full Linux unit run finished with 7,609 passes and one failure: the existing ML spawn worker exceeded its 60-second limit under host load. No timeout or assertion was weakened; the final full rerun passed 7,658 tests.
22. Calculator reload encountered a transient Ranger-backed state-read failure (HTTP 500). The explicit retry restored the saved result 450.
23. The session expired during form editing (HTTP 401). Login restored the form; a fresh submission completed. The rejected request is not counted as a successful trial.
24. The generated form incorrectly described assistant continuation as avoiding external processing. Platform instructions now explicitly distinguish local calculations from submissions processed by the configured model provider. Fresh form generation subsequently displayed the corrected storage/provider explanation.

25. Smart final answers without a subsequent tool checkpoint showed zero tokens. Root completion now records the final cumulative provider usage, without adding checkpointed usage twice; four regression cases cover fresh, partially checkpointed and replayed counts.
26. Direct Stop initially lost its terminal label after history reload. A persisted outcome now restores cancelled/error states even when no answer text exists. Cancellation during journal flush also persists its outcome.
27. Completed interactive answers repeated their raw query tables ahead of the visual. Source tables now share a collapsed disclosure, retaining filtering, export and artifact actions. Unknown catalog versions and failed turns retain expanded source tables.

28. A later public-map request returned prose without its requested program. Missing programs now share the one-repair budget with invalid programs, and terminal fallback explicitly states that no interactive view was prepared. This failed live trial is retained; three later public-map generations passed.
29. Smart Stop before run acceptance previously could disconnect the browser without cancelling the accepted server run. The pending cancellation now waits for the run ID and confirmed cancellation; the component test models delayed acceptance and network abort.
30. The isolated FE restarted with a different container IP and could not elect its persisted identity. Restoring the original isolated-container IP allocation recovered healthy FE/BE; no data or user container was removed. Seed verification then passed.
31. Browser `maxWorkers=1` did not serialize browser files. The overloaded coverage attempt was interrupted (exit 130); the replacement explicitly sets `--browser.fileParallelism=false`.

32. Screenshot review found pale text on the public map’s default white popup in dark mode. Popup surfaces, text and arrow now use Nova popover tokens. Browser component checks verify contrast of at least 4.5:1 in light and dark. The fresh live screenshot confirms readable dark popup text.
33. Evidence review found that object rows were paired with columns by object insertion order. They now bind by column name, omit unselected keys, and preserve missing values as null. A regression checks reordered keys and incomplete rows.
34. The public-map adapter error path retained only a status message. It now retains the validated static place list when the media request fails or its original conversation source is unavailable. Both paths have browser component regressions.
35. A separate final-unit snapshot initially omitted the existing Newsroom workspace fixture, causing seven collection errors. The fixture directory was copied unchanged and the full suite restarted; the incomplete collection is not a pass.
36. The frontend run after the map fallback change timed out in an existing workspace table-popover test while engine and unit suites were active. Its screenshot showed a loading column fixture. The unchanged module passed 6/6 in isolation, and the quieter full rerun passed all 1,267 tests; no timeout or assertion was weakened.
37. The completed full integration run had 18 failures because nested MySQL client containers could not mount runner-local temporary configuration files. The runner now writes temporary files through the existing host-shared directory. All 31 tests in the complete MySQL proxy module plus UI persistence passed afterward; no application code or assertion changed for this repair.

38. Map drill-down identified Bandung but asked the user to interpret row indexing. Nova now resolves selection from the validated persisted program, evidence and active filters. Internal map indices are omitted from the model input; malformed/stale selections cannot invent a row. The first live retry identified Bandung without asking for clarification, but repeated a variable name from earlier history; the fresh retry and final production drill-down identified Bandung without internal indices.
39. Disabled workflow polling repeated 403/404 responses while a turn streamed. Polling now stops for those terminal statuses; browser regressions cover both while preserving retry for transient service failures.

40. A generated form correctly described provider submission but incorrectly claimed that typing sends nothing. Inputs are persisted in Nova, so both prompt contracts and the renderer now state the distinction explicitly: state saves to Nova; continuation invokes the configured provider. The first updated action returned 400,000 reserve and 3,600,000 remaining from 4,000,000; the fresh form at 22:22 displayed the corrected explanation.

41. Immediate Smart Stop returned success, but a concurrent worker heartbeat restored the root to running and work continued. The development worker was stopped, the QA run reconciled through the harness cancellation path, and run-row writes moved from process-local locks to one renewable Redis lock per run, shared by harness and direct journal. Root cancellation also shares admission coordination with delegation and cancels descendants individually under their row locks. This was a blocking correctness finding. Focused tests, three live Smart cancellation trials, and the real-engine root/child heartbeat race now pass.

42. A form revealed a follow-up button after local calculation, but validation had omitted its initially hidden action. Validation now checks every conditional branch, declares all unique actions, refuses fabricated hidden business rows and unsafe hidden HTML, and limits Each to passive text/layout. Action dispatch revalidates the source instead of trusting old action metadata. Reload retained the 5,000,000 → 500,000 / 4,500,000 state; the previously inactive button now starts its agent continuation. Focused backend 59/59 and UI 61/61 passed.
43. A full-unit run on the pre-conditional source was interrupted after 5,364 passing tests to incorporate finding 42. It is not counted as a complete gate; the final full run passed.
44. The fresh combined business-map turn rendered correctly, including Retail = 800, radius 0/700,000, map/table parity, Bandung selection and the exact polygon. Its first detail action exhausted the bounded provider-format repair because the provider emitted a tool call as text. Nova rejected the malformed output; the action trial failed; the retry and final production drill-down passed.
45. The full eval run initially had five failures because recovery test doubles had no Redis run-write coordinator. The two affected modules now install the existing in-memory lock fixture; production fail-closed behavior and recovery assertions remain intact. The complete eval and scorecard were rerun.
46. Frontend lint found two unnecessary quote escapes in a new hidden-sandbox regression. The literal was corrected without changing its represented program. Lint, build and catalog drift then passed.
47. Reload restored the provider-format failure's trace but omitted its visible failure notice. All persisted non-success terminal reasons now restore an error state, and the direct transcript displays the same durable failure notice as Smart. Unit cases cover provider format, context overflow and budget exhaustion; the browser case asserts a visible notice and no resubmission.
48. The session expired before the second map detail action. The rejected request is not a successful trial. Authorized login restored access; the selected Surabaya row was submitted again in the new session.
49. A later Jakarta analysis reached a provider ReadTimeout after its location/region tools. Retrying the original detail action completed from the selected-row snapshot, explicitly distinguished snapshot values from a fresh query, and correctly separated distance from polygon containment. The timeout trial remains failed.
50. Ranger reruns initially used an incorrect Redis port and then omitted its configured authentication. The runner now uses the same backend environment as the development server. The complete patched-FE proxy and agent-tool acceptance passed; no application checks or assertions were relaxed.
51. Form follow-ups at 6,000,000 and 7,000,000 produced the correct total and reserve, but provider prose invented an allocation delta and one generated control order disagreed with its prose. Prompt guidance alone did not eliminate the narrative error in the 8,000,000 follow-up. The validator now exports evaluated simulation rows/calculator values privately to the existing numeric checker. Unsupported prose is removed or rebuilt from those assumptions, without promoting them to business evidence. Direct and Smart trajectories verify that checked prose is emitted and persisted. When reconstruction is necessary, the transcript uses a concise introduction while the UI block retains the complete Markdown fallback.
52. Superseded unit runs were interrupted after 5,466 passes and then 4,159 passes to add simulation consistency and remove duplicated fallback tables. Neither is a completed gate.
53. The 9,000,000 live follow-up corrected unsupported prose, but repeated the result as both Markdown and interactive tables. The transcript now keeps a concise introduction; the complete checked table remains available as the UI block fallback. Focused regressions passed 88/88.
54. The 10,000,000 follow-up used expressions as state defaults. The parser retained their AST as state, producing `[object Object]` and blank amounts. Validation now refuses expression objects in state and explains how to use regular reactive variables. Five catalog cases and a real-loop repair trajectory cover the error. This failed result remains in QA history; it is not a successful trial.

55. Review found that disabling the UI feature would hide the complete rebuilt simulation fallback and retain only its concise introduction. History projection now restores the full checked Markdown for the original security context. A regression also verifies that a changed role cannot recover UI content through this path.

At 23:22–23:25, three final form continuations at 11/12/13 million passed. Slider changes recomputed the first two allocations; changing the third total to 14 million recomputed 4.9/3.5/2.8/1.4/1.4 million and survived reload. These are assumptions, not verified business figures. Currency formatting rounds each displayed component independently; fractional allocations can therefore differ from the displayed total by rounding units.

56. Final review found that the legacy Smart observation/replay check compared owner and role but omitted the security-context version. Shared Smart scope validation now rejects missing or changed versions before observing, cancelling or replaying a run. Fifty focused API/journal cases passed. The exact-source full unit rerun passed; the final run including media transport cases passed 7,658 tests.

57. Packaging review found that the existing Docker context could include local `.env` files through `COPY backend/`. The in-progress local image build was stopped before delivery, environment files were excluded (sample templates retained), and a clean production-image build passed its runtime, hash and exclusion checks. No image was published.

At 23:40–23:42, a fresh Smart calculator survived reload during its run after the security-version hardening. It completed at 100 × 2 = 200 and editing to 150 × 3 immediately produced 450.

58. The first image smoke assertion expected the validator display-text summary to contain only the calculated number. That summary also includes literal expression operands. The corrected smoke test asserts the structured evaluated result (5 × 20 = 100); it passed without application changes. The production image also passed Node/Python runtime and `.env` exclusion checks.

59. Production UI replay initially failed an automation assertion because Online revenue renders as `1,200`, while the assertion searched for `1200`. The assertion now normalizes the grouping separator; all three chart/radius combinations passed. This did not require application changes.
60. One browser-control call timed out while opening a location disclosure, and a later drill-down locator matched buttons from two historical answers. Fresh accessibility state resolved the disclosure, and the continuation was scoped to the original map. These automation failures are retained separately from application outcomes.

Production-build replay at 23:49–23:51 passed three chart/radius combinations and three polygon selection/zoom trials against live saved Nova messages/state. A session expiration required authorized login before this run; the rejected navigation was not counted as a pass. The production drill-down then completed through the configured provider, fetched the location fixture again, and returned Bandung with distance 120258.30094901558 and inside_region = 1, without conflating radius and containment. Console warn/error capture was empty during the final production scenario.

The fresh retry for finding 44 identified Bandung without exposing internal state variables or asking about row indexing. Its SQL values matched the fixture. Reload retained radius 700,000, the selected Bandung row and polygon selection.

At 22:52, three final-renderer chart/radius interaction combinations passed: Retail/0 m = 800/one branch; Partner/700,000 m = 500/three branches; Online/0 m = 1,200/one branch. Three polygon selection/zoom trials retained the exact fixture coordinates. The first observation script mistakenly checked accessibility diffs as full snapshots; it was corrected to request full snapshots and all three assertions passed.

At 23:26, switching from the saved form back to the business thread restored Retail = 800, radius 700,000, Bandung selection and Service area selection. The final form value 14,000,000 was also restored after reload.

The final whitespace-only chart cleanup produced byte-identical production artifacts after lint/build; the browser and coverage results therefore cover the delivered frontend behavior.

The catalog calculation suite covers all 59 callable functions with 60 tests, including expected results, unchanged inputs, bounded growth, zero denominators, numeric precision and UTC date behavior.

The UI now discloses result rows omitted by the 200-row evidence transport cap, independently of the map feature display cap.

## Evidence

- [Live provider calculator](live-calculator.png)
- [Live chart filter](live-chart-filter.png)
- [Live business map selection](live-business-map.png)
- [Live polygon selection and containment](live-polygon.png)
- [Live public map marker](live-public-map.png)
- [Live form follow-up](live-form-followup.png)
- [Live Smart Stop after reload](live-smart-stop.png)
- [Live provider drill-down from production build](live-production-drilldown.png)
- [Production fixture business map, desktop](fixture-light-1440-business-map.png)
- [Production fixture business map, dark 320 px](fixture-dark-320-business-map.png)
- [Production fixture chart, desktop](fixture-light-1440-chart.png)
- [Production fixture chart, dark 320 px](fixture-dark-320-chart.png)
- [Production fixture assertions](fixture-report.json)

Screenshots of fixture data do not substitute for live provider/API testing. Raw server logs, credentials, and provider configuration are excluded from committed evidence.

## Local review environment

Backend/API and the configured Smart worker run locally against the existing governed development stack. Frontend port 5173 serves the verified production build through Vite preview, with API proxying to port 8000. Synthetic `Nova UI QA v2` conversations and resources are retained for review. Credentials and raw execution logs are not included in this report.
