# mapcn migration acceptance record

Implementation complete; validation recorded 21 October 2026, Asia/Jakarta. This record distinguishes deterministic browser fixtures from configured-provider acceptance. No production deployment or GitHub Actions result is claimed.

Baseline: Intelligent UI v2 plus `origin/main` at `4819e1bd`. The feature branch integrated that main commit without conflicts. Mapcn source is pinned at `40c790f739c058f0f0d22b9287d13fe7cfd4a5bf`.

## Gates

Final local run on 21 October 2026 against the delivered tree (feature branch level with `origin/main` at `4819e1bd`).

| Gate | Observed result |
| --- | --- |
| UI catalog/validator drift (`pnpm ui:check`) | Passed |
| Frontend lint | Zero errors. 20 source warnings: 19 existed before this migration; the new one is `react-refresh/only-export-components` for the `useMap` export in the vendored map primitives, the same pattern as `button.tsx` and `form.tsx` |
| Frontend build | Passed |
| Frontend coverage (`pnpm test:coverage`) | 158 files, 1,379 tests passed |
| Production browser fixture (`pnpm ui:verify`) | 26 cases passed: both themes, desktop/tablet/320 px, reflow, source table, routes/arcs, advanced controls, blocked external networking, WebGL failure |
| Backend Ruff (changed files) and full mypy | Passed |
| Backend unit coverage | 7,569 passed locally. Five ML files cannot import the locked scipy wheel on Darwin 27; they passed in a Linux `python:3.11.13-slim` container (101 tests) |
| Agent harness eval | 48/48 scenarios, 165/165 checks |
| Seeded real-engine integration (`-m engine`) | 214 passed, 16 skipped (Ranger, shared-data and opt-in fixtures), 1 failed. `test_intelligent_ui_persistence.py` passed. The failure, `test_bootstrap_and_review_use_production_api_with_restricted_identity`, refuses a reused engine ("Outcome training already exists; use a fresh isolated engine"). `test_native_ml_arrow_l3.py` was not run locally because of the scipy import failure |
| Same-host benchmark, 10 trials at 768 px | Ready median 965 ms legacy vs 918 ms mapcn (p95 1,873 vs 985 ms); selection median 43 vs 51 ms (p95 69 vs 60 ms) |
| Live configured-provider UI | Passed after three fixes (see below and [live evidence](live/)) |

## Failures retained

1. Initial prompt-example tests lacked the newly referenced evidence IDs. Examples now use the established sample evidence ID, and the fixture includes actual route and destination fields.
2. A camera test expected the validator to return saved state. The validator intentionally returns program defaults; the assertion now tests acceptance/rejection of saved cameras.
3. Popup keyboard acceptance found missing focus restoration. Popup lifecycle listeners now register before opening, and marker popups restore their trigger.
4. A resource failure could fire during Map construction before listeners existed. Styles now load after listener registration; failed initialization ends the loader and exposes fallback.
5. Build identified un-narrowed Point collections and an unnecessary direct GeoJSON type import. Collections now narrow by geometry type; MapLibre's existing GeoJSON types are used.
6. Initial Ruff/mypy identified import ordering, long lines and an overly narrow inferred route-result dictionary. These were corrected without changing test thresholds.
7. Visual review found map actions covering route endpoints and an unthemed attribution chip in dark mode. Actions now render above the canvas, controls moved top-right, fits pad for controls and attribution, and attribution uses Nova tokens.
8. Lint review moved the business style and request guard out of the map surface component file into `map-style.ts`.
9. Live provider: the first streamed GeoMap fitted before the answer's width settled, leaving two of three locations off-canvas until reload. The camera now follows data bounds and refits on resize until the user takes over; a real-MapLibre browser test fails without the fix.
10. Live provider: `$viewport` and `$routeSelected` never saved, because `bound.optional()` lost the reactive marker and components received plain values. The optional schema is now marked reactive and the validator bundle regenerated; a renderer test fails without the fix.
11. Live screenshots showed Show all keeping a tilt, which pushed a location under the controls. Show all now fits flat and north-up, and fit padding clears numbered markers.

12. Product decision after live review: the right-hand navigation column (zoom, compass, locate, fullscreen) was removed from Studio maps. Fit padding no longer reserves room for it, and a browser test asserts none of those controls render.
13. A live state probe found every page load re-saving an unchanged `$viewport`, because the server returns object keys sorted and the client compared state with order-sensitive JSON. Revisions climbed on each visit and concurrent tabs could conflict. State is now compared by content; a renderer test fails without the fix.
14. Product decisions after live review: public maps gained a 3D view (OpenFreeMap Liberty, pitch 60°, as in mapcn's example), and routine save labels were removed because they shifted the answer on every camera move. Save failures and conflicts still show. Browser tests cover the missing 3D control on business maps and the absence of save labels during a camera save.

## Live configured-provider acceptance

Run 21 October 2026 on the local stack (backend, Smart worker, preview build) as `nova_admin` with the configured provider, agent "Nova UI QA v2". Screenshots are in [live/](live/), captured headless at 2× after the final build.

| Check | Result |
| --- | --- |
| Model emits v3 catalog | `nova-openui-3` GeoMap with `{controls, advanced, popupFields}` and `$viewport`, accepted by server validation |
| First streamed render | All three tool locations in view without reload |
| Selection and popup | Selecting Bandung shows `distance_m`/`inside_region` from tool rows; `$selected` saved |
| Camera persistence | Tilt saved as `$viewport` (pitch 45) and restored after reload |
| Assistant action | Follow-up resolved the selected Bandung row from the scoped source |
| Public route | Two OSRM alternatives (169.9 km / 170.9 km), `$routeSelected` saved, media request excludes viewport and selection |
| Basemaps | Public maps load CARTO Positron/Dark Matter with CARTO and OpenStreetMap attribution; business map pages request no CARTO or other map host (only the app shell's existing Google Fonts) |
| Legacy v2 answer | Earlier v2 thread renders with mapcn and fits all locations |
| Themes and widths | Dark and light at 1440 px, dark at 390 px |

The provider once returned a tool call as text ("The response contained a tool call as text"); the harness refused it and a retry succeeded. A camera restored on a much narrower screen keeps the saved centre and zoom, so it can crop locations until Show all is pressed.

All browser examples use synthetic data. Existing Nova business fixtures and provider configuration are reserved for live acceptance through the application's authenticated path. Fixture success is not counted as a live-provider result.

## Reproduction

From `frontend/`: `pnpm lint`, `pnpm build`, `pnpm test:coverage --browser.fileParallelism=false`, `pnpm ui:check`, `pnpm ui:verify`, and `node scripts/benchmark-studio-maps.mjs`.

Backend commands and prerequisite environments follow the repository's backend and Docker guides. Coverage XML for this migration is written outside the tracked baseline report.

The map resource, catalog compatibility and rollout contracts are documented in [the architecture record](../../architecture/studio-mapcn.md).
