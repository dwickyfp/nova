# Studio maps with mapcn

Nova adapts [mapcn](https://github.com/AnmolSaini16/mapcn/tree/40c790f739c058f0f0d22b9287d13fe7cfd4a5bf), pinned at `40c790f739c058f0f0d22b9287d13fe7cfd4a5bf`, on the existing MapLibre 6.13.0 runtime. The upstream MIT notice is bundled in `frontend/public/licenses/mapcn.txt`. This migration changes presentation and map interactions; it does not introduce a tile server, routing engine, inference loop, or metadata database.

## Component mapping

| Upstream component | Nova use |
| --- | --- |
| Map, MapRef, useMap | Lazy shared map surface, controlled camera, trusted application layers |
| MapControls | Not rendered: Studio maps show no zoom, compass, locate or fullscreen buttons |
| MapMarker, MarkerContent | Public numbered places, route endpoints and progress markers |
| MarkerLabel, MarkerTooltip | Public place names, hover and keyboard descriptions |
| MarkerPopup, MapPopup | Public place details and selected business-row fields |
| MapRoute | Verified LineStrings or public routing-adapter results |
| RouteProgress, RouteMarker | Evidence-backed progress, or explicitly labelled public simulation |
| MapArc | Verified origin–destination relationships; never a measured road distance |
| MapGeoJSON | Validated Point/LineString/Polygon evidence with linked selection |
| MapClusterLayer | Local point clustering, count glyphs and expansion |
| Advanced APIs | Trusted custom layers, tilt/reset, viewport and layer events |

These primitives live under `frontend/src/components/ui/map/`. The model receives the bounded Nova catalog, not React callbacks or the MapLibre API. Upstream ideas such as live tracking, drawing, geofencing, heatmaps and 3D buildings are outside this migration.

## Resource and design adaptation

- Worker code remains bundled. Upstream CDN worker fallback and automatic CARTO styles are removed.
- Business maps use bundled Natural Earth land geometry and bundled Noto Sans Regular count glyphs. Their request transform rejects every cross-origin resource, including after a theme change.
- Noto glyphs and their license originate from the [MapLibre demo font distribution](https://github.com/maplibre/demotiles/tree/gh-pages/font/Noto%20Sans%20Regular); only the 0–255 range used by cluster counts is bundled.
- Public maps use the CARTO basemaps from the mapcn examples: Positron for light and Dark Matter for dark, with CARTO and OpenStreetMap attribution from the tile source. Business maps never load them, because tile requests reveal the viewed area. Public maps also offer a 3D view toggle, matching mapcn's "OpenStreetMap 3D" example: it switches to OpenFreeMap Liberty, which carries extruded buildings from zoom 14, and eases to pitch 60° and at least zoom 15. Show all and Reset leave 3D, and a saved camera at 60° reopens in 3D. Liberty has no dark variant. Business maps have no 3D view. The legacy renderer behind the rollback flag keeps OpenFreeMap. Public driving routes retain the existing bounded OSRM adapter.
- Colors resolve Nova semantic tokens to MapLibre-compatible RGBA. Popup and attribution CSS is scoped to `.nova-map`; controls use Nova surfaces and visible keyboard focus.
- Map actions (show all, map view, tilt, reset) render above the canvas so they never cover evidence. The canvas has no navigation buttons; pan, scroll/pinch zoom and keyboard navigation remain. Camera fits pad for the attribution and half a numbered marker. Show all returns to a flat, north-up overview.
- Camera animation respects reduced motion. Popup Escape handling restores its trigger's focus. Failed initialization and resources provide visible outcomes.
- The business basemap remains an overview. A different React component does not add street-level cartographic detail.

## Catalog and state

`nova-openui-3` adds optional map configuration after existing positional arguments:

```text
GeoMap(data, longitude, latitude, geometry, label, title, selected, options?, viewport?)
Map(markers, route, label, options?, viewport?, routeSelected?)
MapMarker(latitude, longitude, label)
```

Options are a strict object. Supported keys are `controls`, `advanced`, `locate`, `editable`, `simulation`, `popupFields`, `layer`, `progress`, `toLongitude` and `toLatitude`. `layer` is `geometry`, `route` or `arc`. Column names bind existing evidence; they cannot contain code, URLs, paint expressions, or new result rows.

Business route mode requires a geometry column containing LineStrings. Progress is a numeric evidence column in [0, 1]. Arc mode requires explicit origin and destination longitude/latitude columns. Invalid geometry, missing endpoints or invalid progress remain excluded and counted. Generated arc samples share the geometry display budget.

Public `locate`, `editable` and `simulation` options are refused on business maps. `locate` is still accepted on public maps so saved programs validate, but it has no effect and is no longer offered to the model: maps never request browser geolocation. Dragging public markers creates temporary assumptions, hides the original route, provides numeric keyboard inputs, and offers restoration. It does not request a new route.

Viewport state is either null or `{center: [longitude, latitude], zoom, bearing, pitch}`. Bounds are validated both in the shared validator and before v3 state persistence. Route selection is an integer from 0 through 2. Camera state is committed on movement end through the existing debounced, revision-checked UI state service. Saving is silent: routine progress (loading, unsaved, saving, saved) is not shown, while a failed save or a conflict with another device still shows its message and Retry or Reload action. State is compared by content, so reopening an answer does not re-save it. Internal presentation variables are removed from assistant action context.

Initial/restored camera and dataset bounds are applied once per dataset. Until the user moves the camera (gesture, map action or selection), it follows the data bounds and refits when the canvas resizes, as it does while a streamed answer settles; that automatic camera is not committed to state. A restored viewport is the user's and is not refit. Hover, popup and unrelated renders do not reset the camera.

`viewport` and `routeSelected` are optional `$` bindings. The catalog marks the optional schema itself reactive, because lang-core recognises a binding by schema identity and `.optional()` returns a new schema. Theme changes replace styles while preserving viewport. Selection updates the table and popup; existing assistant actions resolve selected rows from the scoped source.

The archived v2 catalog and validator remain immutable compatibility artifacts. They validate old programs independently; old hashes and saved state are not rewritten. Unsupported versions retain Markdown fallback. Shared geometry bug fixes must preserve the archived v2 behavior and receive compatibility tests.

## Routing and observations

Public routing returns at most three alternatives, with a cumulative 5,000-position limit and the existing response-byte bound. Non-finite or negative distance/duration values are refused. The original `geometry` response remains available for older consumers. Duration is displayed as an estimate; progress simulation is not tracking evidence.

Map clients report only `ready`, `failed`, `fallback` or `route_unavailable` plus a bounded elapsed time. The existing scoped UI-source authorization precedes recording into Studio workflow metrics. Metrics distinguish client observations from server validation, and contain no coordinates, labels or viewport. Public-media caches use a new namespace for the expanded response.

## Rollout

`VITE_STUDIO_MAPCN_ENABLED` defaults to true. Set it to `false` and rebuild to use the legacy renderer for v2 content and Markdown fallback for v3 content. Both application versions must retain the v2 validator artifacts.

The existing Intelligent UI feature flag remains the outer gate. No composer, Smart-mode, SQL authorization or agent-loop behavior changes are required. See the [acceptance record](../testing/studio-mapcn/README.md) for validation status and limitations.
