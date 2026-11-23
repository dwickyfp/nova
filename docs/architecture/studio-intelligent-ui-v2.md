# Studio interactive answers and offline harness experiments

Status: implemented and locally validated. See the [validation record](../testing/intelligent-ui-v2/README.md) for exact gates, failed attempts, environment-specific skips and live screenshots. Production deployment and GitHub Actions results are not asserted.

## Research and adaptation

The implementation starts from `main` at `d068264f`, after the reversal of #185. It selectively retains the previous parser, evidence, persistence, action, and sandbox boundaries. It does not restore the old composer or wide card layout.

| Reference | Finding | Nova adaptation | Existing owner |
| --- | --- | --- | --- |
| [Open Intelligent UI, pinned source](https://github.com/thesysdev/open-intelligent-ui/tree/03213439358bf3408ad61a46ec9e3230c5572e48) | A component catalog and reactive variables let prose and useful controls form one answer. | Versioned layout catalog (`nova-layout-1`) streamed per statement; pages with KPIs, native charts, tables and local what-if controls. | `frontend/src/features/studio/intelligent-ui/`, assistant compositor |
| [Library and prompt generation](https://github.com/thesysdev/open-intelligent-ui/blob/03213439358bf3408ad61a46ec9e3230c5572e48/src/lib/library.ts) | The model needs component signatures and examples matching the renderer. | One Zod source generates the catalog JSON the Python checker and prompt use; a drift check guards it. | `runtime/components/layout.ts`, `assistant/ui_layout.py` |
| [Public map routing](https://github.com/thesysdev/open-intelligent-ui/blob/03213439358bf3408ad61a46ec9e3230c5572e48/src/app/api/street-route/route.ts) | Public location enrichment has external network dependencies. | Public routes and images are fetched by the server while it checks the page statement and are stored in the program (`ui_public.py`); the browser never calls OSRM or Commons search. Business GeoMap keeps its own rules. | backend `ui_public.py`, frontend `place-map.tsx` |
| [Harness², pinned source](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335) | An agent harness is an editable component tree (prompt, guardrails, tool guidance, skills, scripts, hooks) refined by propose–probe–compose with blind selection, reflection verdicts and a lesson library. | A typed, bounded harness policy per agent (standing rules, tool guidance, in-loop guards) beside skills, procedures, memory and budget. One offline cycle: baseline, two proposals (how the agent works; data knowledge), their probes, one composition and probe, then one reflected lesson. Probe tools remain read-only. | agents `harness.py`, `harness_experiments.py`; assistant `guards.py`; existing quality engine |
| [Harness² experiment protocol](https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/docs/EXPERIMENTS.md) | Reproducible experiments require provenance and resumable stage records. | Version/manifest/case fingerprints, deterministic operation identity, stage checkpoints, latency/token budgets and lease fencing. Baseline wins ties and incomplete comparisons. No automatic production promotion. | quality records, versions and release manifests |

Harness² is an optimization reference, not a replacement for Nova's runtime. Published benchmark gains from that project are not measurements of Nova.

| Behavior | Baseline `d068264f` | Implemented behavior under acceptance |
| --- | --- | --- |
| Composer mode | Agent-oriented selection | One selected agent per chat; original composer styling. The Smart toggle was removed on 2026-11-07. |
| Interactive answers | Markdown and existing result artifacts | One renderer for every Studio visual: model programs, deterministic maps/KPIs, tool charts, artifacts and dashboard tiles; Markdown fallback retained |
| UI continuation | No versioned UI state/action contract | Retired in F5: page controls are local and unsaved; follow-ups are ordinary messages |
| Direct run reload/Stop | In-memory reconnect; Stop disconnected the browser | Discover/replay the scoped journal after reload; explicit server cancellation |
| Recovery allowance | Per-attempt time and repair handling | Absolute deadline, consumed tokens and shared repair counts survive checkpoints |
| Harness improvement | Existing quality evaluation and release workflow | Agent harness policy and guards, offline propose–probe–compose records with baseline retention, a per-agent lesson library and existing promotion gates |

## UI contract: page layouts (`nova-layout-1`)

The interactive answer is one page written in the OpenUI Lang layout
language, inside one registry component. The earlier `nova-openui-2/3`
programs, the Node validator, catalog v2 and their saved state and action
routes were retired in F5; see [Legacy answers](#legacy-answers).

**Catalog.** The single source is the Zod library in
`frontend/src/features/studio/runtime/components/layout.ts`. `pnpm components:bundle`
exports it into `backend/app/modules/assistant/ui_components.json` (key
`layout`); `pnpm components:check` and CI fail on drift. Components: `Page`,
`Header`, `Section`, `Row`, `Text`, `KpiTiles`/`Metric` (value, format,
change, badge, digits), `DatavizChart`, `ResultTable`, `GeoMap`, `PlaceMap`,
`Picture`, `Controls`, `Slider`, `Segmented` and `Toggle`.

**What the model writes.** In a fenced `openui` block after its prose, the
model writes one statement per line. It may use `Chart(evidence, visual, x,
y, title)`, `Table(evidence, columns, title)`, `DataMap(evidence, latitude,
longitude, label, measure, title)`, `RouteMap(title, places, route,
optimize)`, `Image(url, alt)`/`SearchImage(query, alt)` and `@Value(evidence,
column, aggregation, filter)` for verified figures; it never writes a business
number as a literal. `$variables` take literal defaults and drive scenario
expressions.

**Checked per statement, in Python only.** `assistant/ui_layout.py` parses
each complete statement as it streams, checks it against the catalog and the
turn's verified results, and rewrites it:

- `Chart` becomes `DatavizChart(title, chart, draft_ref)`: a native Dataviz
  draft built through the governed draft query of that result. The draft
  reference is signed by the server; the governed source stays on the server.
- `Table` becomes `ResultTable(title, columns, rows)` with bounded rows of
  that result.
- `DataMap` becomes `GeoMap(map)`: the shared business map over at most 200
  of the result's rows with valid coordinates. Only the named columns are
  kept, credential columns and values are dropped, and the tooltip shows the
  label and the one measure. Without named columns it reads the result the
  way an answer without a page does (coordinates, WKT or GeoJSON).
- `RouteMap` becomes `PlaceMap(title, places, routes, unavailable,
  optimized)` (`assistant/ui_public.py`). Places are public places typed by
  the model, so each coordinate is checked: in range, and when its label
  names a known city it must lie within 60 km of that city, or it is moved to
  the city centre (`corrected`). A place Nova cannot check is drawn as
  `approximate` with a dashed ring. When a route is asked for, the server
  calls the public OSRM `route/v1/driving` service during the statement
  check (8 s cap, up to three alternatives between two places, polyline
  geometry simplified to about 30 m and at most 5,000 positions in all).
  Only city-level positions (two decimals) are sent. `optimize` orders the
  visit from the first place by the OSRM duration table (exact up to 12
  places). Places on different known islands get no route, because the
  public server would route through ferries; a missing road or a timeout
  also leaves the places on the map with the reason. The geometry is stored
  in the program, so the browser never calls OSRM and a replay works
  offline. The user can switch between the stored alternatives, each with
  its distance and driving time.
- `Image` and `SearchImage` become `Picture(url, alt, source)`. The image
  must be an `https://upload.wikimedia.org/` upload; a search uses the
  Wikimedia Commons API. The browser loads only that image and credits its
  Commons page.
- `@Value` becomes the number computed from the verified rows. A typed
  business number, an unknown result or column, a filter that matches no row,
  a forged draft reference or an unknown component refuses that statement
  only.
- Text clauses with figures the evidence does not support are removed, using
  the existing numeric checker.

**Streaming.** v1 sends `ui` to open the block, one `ui_delta` per accepted
statement and `content_block_done`; v2 maps these to `part.start`,
`part.delta` and `part.end`. A page written before the agent decided to call
a tool is emptied with `ui_delta {replace: true, text: ""}`. The browser
renders with `@openuidev/react-lang` and its streaming parser, so the header
appears first and each section as it arrives. Invalid or partial statements
degrade per statement. The stored step holds exactly the program that
streamed, so a reload replays the same page.

**When a page is drawn.** The planner chooses `presentation: interactive`
for dashboards, overviews, comparisons of several figures, results side by
side and what-if questions over verified figures. A single figure, a short
list or a plain question stays text; Nova's deterministic views (below)
still draw one chart, ranking, trend, grid, map or metric by themselves.
The run records `ui_validity` (statements, errors) and `ui_evidence`.

**Controls.** Sliders, segmented choices and toggles recompute expressions in
the browser over figures already on the page. They send nothing, issue no
query and are not saved; a reloaded page starts from the defaults the agent
wrote. Verified figures do not depend on controls.

**Currency.** Neither prompt asks the model to remark on a currency or unit
the evidence does not name. The earlier "The data does not specify a
currency." sentence came from the final composer treating a missing unit as
an uncertainty to disclose; `test_the_final_composer_does_not_invite_currency_remarks`
covers the wording.

## One Studio renderer

- **Pages.** `runtime/components/layout-view.tsx` renders `nova-layout-1`
  blocks in Studio and Neva.
- **Registry components.** Native `DatavizChart`, `KpiTiles`, `GeoMap` and
  the rest render from `runtime/components/registry.tsx`; a page uses the same
  `DatavizChart` and the same KPI tile (`figure-tile.tsx`).
- **Deterministic views.** When an answer has no page,
  `assistant/ui_autocompose.py` adds one view for its displayed verified
  results without a provider call: a governed `GeoMap` (points, routes and
  ordered stops), `KpiTiles` for one-row measures, a native `DatavizChart` for
  trends and rankings, or a grid page (`root = Page([grid])`) for detail rows.
- **Stored charts.** `data_to_chart`, saved artifacts and dashboard tiles
  keep Vega-Lite v5 as their stored template. `runtime/components/spec-chart.ts`
  turns a spec and its rows into a chart or table plan for the same chart
  view; unsupported transforms fall back to a table of the original rows.
- **Tables.** `result-table.tsx` is the only result grid.
- **Saving.** Page charts are Dataviz drafts and save through the artifact
  panel. Each composed block still records `provenance.tool_calls` and, for
  results fetched elsewhere, `provenance.statements`.

## Legacy answers

Messages stored with `nova-openui-2` or `nova-openui-3` blocks are translated
when read (`assistant/ui_legacy.py`), never rewritten: KPIs become `Metric`
tiles, natively drawn charts keep their `draft_ref`, other data views become
`ResultTable`s of the stored rows. Both the stored step and its event journal
are translated, so there is no second runtime. The page is marked
`provenance.legacy` and shown read-only; old forms, actions, media and saved
state are not carried over.

Studio chats with one selected agent at a time. A new chat opens the agent the user last chatted with, remembered per browser, and otherwise the first agent. The existing composer dimensions and styling remain intact.

Activity is summarized in one disclosure. Tools and evidence remain inspectable; consent and actionable failures remain visible. Answers use the normal transcript width and prose spacing. Controls can be grouped, without wrapping each answer in a second card. Completed interactive answers group raw query tables in one source-data disclosure; filtering, export and artifact actions remain available. Unknown catalog versions and failed turns keep source tables expanded.

## Charts and geospatial

Charts support bar, line, area, pie/donut and scatter, tooltips, series selection and a companion table. Brush zoom is available where the data shape makes it meaningful. Chart/map code is loaded lazily.

GeoMap accepts longitude/latitude columns or validated WKT/GeoJSON Point, LineString and Polygon. Coordinates must be finite, two-dimensional and within longitude ±180 / latitude ±90. Rings must be closed with at least four positions. The renderer does not guess locations or swap coordinates. SQL DECIMAL coordinate columns may use strictly formatted decimal strings; booleans, blank strings, exponent/hex/comma formats and out-of-range values are rejected. GeoJSON positions require numeric values. It caps display at 1,000 rows and 20,000 positions; invalid and omitted counts remain visible, with invalid rows identified in the table.

MapLibre renders point clusters, lines and polygon outlines/fills. Selection is declarative and linked to the location table. The table remains usable if WebGL fails. Radius and containment results must originate in Semantic Views or configured SQL tools through Nova's SQL frontend. Tests use the pinned StarRocks 4.1.4 functions; browser geometry rendering does not establish analytical containment or distance evidence.

Business basemap: bundled Natural Earth 1:110m land geometry, public domain. Attribution is visible; licensing/source information is in `frontend/public/licenses/natural-earth.txt`. It is an overview, not a street map. No business coordinates, labels or viewport go to external tile, routing, glyph or geocoding services.

## Runtime harness changes

Nova already owns bounded inference, tool authorization, consent, role revalidation, evidence, cancellation and journaled recovery. The changes extend those owners:

- Checkpoints persist remaining turn time, an absolute deadline and consumed measured tokens, alongside existing repairs, tool uses, evidence and operation identities. Downtime cannot replenish the allowance. A budgeted recovery without a valid token counter fails closed before another provider call.
- Provider retries, tool repairs and UI repair share six repair slots, with stricter per-kind limits. Existing terminal authorization/policy/consent failures do not become retryable. Legacy counts consume the allowance; malformed counts fail closed.
- Run-row writes share a renewable Redis lock across API processes, workers and the direct journal. StarRocks updates rewrite whole rows, so a process-local lock cannot prevent a concurrent heartbeat from restoring a cancelled state. A missing or lost coordinator fails closed; an uncertain write is not automatically replayed.
- Existing generation/lease fencing and uncertain mutation replay guards remain in the shared engine. UI does not execute a parallel inference loop.
- Direct-agent reload discovers the running journal only for the same principal, role and security-context version, then subscribes without reposting the message. Stop requests cancellation through Redis and waits for the server's terminal event. A disconnected browser alone does not cancel work. Persisted terminal outcomes preserve stopped/error labels after history reload, including cancellation during journal writes.
- A StarRocks planner timeout while polling worker metadata uses bounded exponential backoff, retaining active tasks. Other syntax/access failures remain terminal. This retry does not replay tools or uncertain mutations.

## Agent harness surface and guards

Live UI acceptance with a data agent is recorded in the [agent harness acceptance record](../testing/agent-harness/README.md).

`GET /api/v1/agents/{agent_id}/harness` reports every component an agent runs with, how it loads and what it costs: instructions, standing rules and tool guidance (in the prompt every turn), skills and procedures (on demand), guards (inside the tool loop), memory (retrieved) and the budget profile (limits). It also returns validation problems. Improve → Harness shows the same census and edits the policy as a draft version; publishing goes through version history.

The policy (`harness_policy` on `CONFIG_AGENTS`) is validated: at most 12 standing rules and 16 tool-guidance entries; 4,000 always-on characters in total; no text that overrides platform policy, skips consent, reveals secrets or carries credential-shaped values; guard rule ids unique and not a platform id. Guidance for a tool the agent does not have is a warning, and an error in experiment candidates. It renders as a bounded `<AGENT_HARNESS>` section only for tools the turn has.

Guards run in the shared `AssistantLoop`, so the model cannot skip them:

- An agent `before_tool` rule refuses a matching call before consent; the user is never asked to approve a call the agent's own rules refuse. The model receives a recoverable `GUARD_BLOCKED` envelope with the rule's message. A refusal is a policy outcome: a required tool a guard refused is not demanded or forced again, and a data turn with no other evidence answers with the rule instead of numbers.
- An agent `after_tool_error` rule appends its message to a matching failure for the model.
- The platform rule `untrusted-instructions-in-data` labels result values that try to instruct the agent (English and Indonesian patterns, bounded scan) and tells the model to treat them as data.
- The platform rule `credential-in-answer` replaces credential-shaped values in the final, clarification and scope text.

Each decision is a `guard` step and SSE event shown in the Studio rail and the trace view, a `guard_events` quality fact for the `guard_compliance` scorer, and, except hints, a redacted `AGENT`/`GUARD` audit row. Budget profiles also cap the measured tokens of one turn. A harness-only edit does not invalidate a verified access grant: the fingerprint hashes every configuration field except edit timestamps, loop limits and the harness policy.

## Offline experiment contract

`POST /api/v1/agents/{agent_id}/quality/harness-experiments` accepts an operation ID, baseline version, distinct case IDs, and explicit time/token ceilings (defaults 1,800 seconds and 400,000 tokens, at most 3,600 seconds and 2,000,000 tokens). It starts the experiment in the background under a Redis lease and returns the running record; the list endpoint reports progress per stage, and a running record without its lease reads as interrupted. It uses the existing quality runs metadata/storage path.

Token accounting includes measured planner and response calls. Missing usage stops a budgeted evaluation; exhausted budgets prevent another loop iteration or experiment stage. A final provider response may cross the remaining allowance; its observed usage is retained and the experiment falls back to baseline. This is a measured execution budget, not a provider billing cap.

Each stage records its in-flight marker before execution and its result afterward. Completed stages are reused on resume. An interrupted stage with an uncertain result is marked unavailable and requires a new operation; it is not silently rerun. Redis lease renewal fences checkpoints. Changing inputs under the same operation ID returns a conflict.

Proposals see a trajectory of each baseline case (actions, observations, guard decisions, errors and scorer outcomes, never expected values) and the agent's active lessons. A candidate edits only the harness policy and appends response instructions: standing rules, tool guidance and guards can be added or removed. It cannot change tools, model/provider configuration, resources, permissions, credentials or executable capabilities. A candidate that fails validation gets one retry with the validator's messages, then keeps the baseline. Proposals use `AssistantLoop` with an empty tool registry; probes use the existing read-only evaluation registry and release pin validation. Composition sees each candidate's per-assertion changes, not private grading answers.

Selection requires the same case/scorer set, a complete eligible candidate, no loss of a passing assertion, and strictly more passes. Ties, invalid proposals, uncertain stages and incomplete runs retain baseline. Each candidate gets a verdict (helped, hurt, no observed effect, confounded, kept baseline). A reflection step may store one lesson (cue, lesson, evidence, confidence) in `CONFIG_AGENT_HARNESS_LESSONS` when the measured results support it; owners can retire a lesson. The recommendation is an immutable candidate version; ordinary draft/version/release approval gates still govern promotion.

UI validity, evidence binding and source-validated action completion extend quality facts/scorers (`nova-agent-scorers:2`). A cancelled, interrupted or failed action cannot score as completed. The shared engine records first-useful-content latency with bounded labels. UI validation, repair and fallback use the existing bounded Studio operation metrics; existing run traces retain usage, latency and recovery outcomes. Metrics labels contain no programs, coordinates, state values or credentials.
