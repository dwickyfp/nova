# Studio interactive answers and offline harness experiments

Status: implemented and locally validated. See the [validation record](../testing/intelligent-ui-v2/README.md) for exact gates, failed attempts, environment-specific skips and live screenshots. Production deployment and GitHub Actions results are not asserted.

## Research and adaptation

The implementation starts from `main` at `d068264f`, after the reversal of #185. It selectively retains the previous parser, evidence, persistence, action, and sandbox boundaries. It does not restore the old composer or wide card layout.

| Reference | Finding | Nova adaptation | Existing owner |
| --- | --- | --- | --- |
| [Open Intelligent UI, pinned source](https://github.com/thesysdev/open-intelligent-ui/tree/03213439358bf3408ad61a46ec9e3230c5572e48) | A component catalog and reactive variables let prose and useful controls form one answer. | Versioned Nova catalog and generated shared validator; inline transcript blocks, local filters, tables, forms and assumption tools. | `frontend/src/features/studio/intelligent-ui/`, assistant compositor |
| [Library and prompt generation](https://github.com/thesysdev/open-intelligent-ui/blob/03213439358bf3408ad61a46ec9e3230c5572e48/src/lib/library.ts) | The model needs component signatures and examples matching the renderer. | Generate catalog, prompt and isolated Node validator together; drift gate checks the deployed bundle hash. Five examples cover business charts, public maps, governed maps, assumptions and follow-up forms. | `frontend/scripts/build-ui-validator.mjs` |
| [Public map routing](https://github.com/thesysdev/open-intelligent-ui/blob/03213439358bf3408ad61a46ec9e3230c5572e48/src/app/api/street-route/route.ts) | Public location enrichment has external network dependencies. | Public media uses restricted server adapters. Business GeoMap is separate, uses bundled land geometry and refuses external resources. | assistant `ui_media.py`, frontend `geo-map.tsx` |
| [Harness², pinned source](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335) | Propose–probe–compose compares instruction changes with observed trajectories. | One offline cycle: baseline, two proposals/probes, one composition/probe. Only instructions change; probe tools remain read-only. | agents `harness_experiments.py`, existing quality engine |
| [Harness² experiment protocol](https://github.com/google-research/harness2/blob/1cfe254e8d25b15f37048c3730134686abe60335/docs/EXPERIMENTS.md) | Reproducible experiments require provenance and resumable stage records. | Version/manifest/case fingerprints, deterministic operation identity, stage checkpoints, latency/token budgets and lease fencing. Baseline wins ties and incomplete comparisons. No automatic production promotion. | quality records, versions and release manifests |

Harness² is an optimization reference, not a replacement for Nova's runtime. Published benchmark gains from that project are not measurements of Nova.

| Behavior | Baseline `d068264f` | Implemented behavior under acceptance |
| --- | --- | --- |
| Composer mode | Agent-oriented selection | Default Smart brain toggle; manual selector only when disabled; original composer styling |
| Interactive answers | Markdown and existing result artifacts | One renderer for every Studio visual: model programs, deterministic maps/KPIs, tool charts, artifacts and dashboard tiles; Markdown fallback retained |
| UI continuation | No versioned UI state/action contract | Source-bound revisions and idempotent action operations through the agent endpoint |
| Direct run reload/Stop | In-memory reconnect; Stop disconnected the browser | Discover/replay the scoped journal after reload; explicit server cancellation |
| Recovery allowance | Per-attempt time and repair handling | Absolute deadline, consumed tokens and shared repair counts survive checkpoints |
| Harness improvement | Existing quality evaluation and release workflow | Offline propose–probe–compose records with baseline retention and existing promotion gates |

## UI contract

Studio makes interactive presentation available by default within its existing
shared assistant loop. The planner's `text` choice is a starting preference:
the answering model can still use the catalog when controls or visuals help.
Ordinary prose needs no UI block or extra composition call. Explicit interactive
plans retain final composition and missing-program repair. Feature disablement,
runtime availability, context budgets, evidence validation and caller
authorization remain enforced.

The `nova-openui-2` block contains `catalog_version`, `program_sha256`, `program`, `fallback`, `state_defaults`, `evidence`, declared `actions`, and `provenance`. Transcript blocks retain `content_id` and `content_index`; old Markdown/table/chart records remain readable.

The catalog prompt asks for Markdown followed by exactly one fenced `openui` program. A program a model leaves unfenced, or in a bare fence, is still taken out of the Markdown and validated, so it is never shown as text. The backend parses programs in a bounded Node subprocess with code generation disabled. The frontend uses the same catalog/validator source. Programs are limited to 64,000 UTF-8 bytes, state to 16,384 bytes, and validator input to 512,000 bytes. Validation takes at most five seconds including admission to its semaphore. State defaults contain literal declarative values only; expression ASTs are refused. Derived calculations use ordinary reactive variables, separate from editable state. Controls remain passive until the full server-validated block is sealed. Validation inspects all conditional branches, including initially hidden controls, evidence bindings and sandbox content. Action IDs are unique across branches. Each repeats passive text/layout only; controls, media and business visuals are declared explicitly. One UI repair is allowed within the shared turn allowance, including when an interactive request omits its program entirely. Failure leaves the Markdown answer with an explicit notice that no interactive view is available.

Business visuals bind `@Evidence("result-id")` to exact verified rows. Displayed prose/numbers also pass the answer verifier. Literals are appropriate for explicitly marked assumptions and public content, not a substitute for result evidence. Local functions only transform supplied rows. They cannot issue queries. A new query or business action returns through the existing agent endpoint and consent boundary.

For simulations with bounded chart/table rows or displayed arithmetic expressions, the validator also evaluates the calculation values. The existing numeric checker compares accompanying prose with these values. Unsupported numerical clauses are removed; conflicting Markdown tables are rebuilt from the computed assumptions. This check does not verify the assumptions as business facts or grant them evidence IDs. The block retains the complete checked Markdown fallback. When that fallback contains rebuilt tables, the inline transcript uses a short introduction so it does not repeat the interactive table. The run trace records the adjustment.

Evidence transport caps each result at 200 rows and records the omitted row count in provenance. Object rows bind by column name, independently of key insertion order; missing values remain null. The renderer shows the omitted count explicitly. That transport limit applies before a chart/map's own rendering limit; local filtering never implies access to rows outside the transported result.

UI source and state are scoped to owner, active role, message, content and program hash. The security-context version counts role changes within one sign-in and restarts with every session, so it separates stamps only within the same session; a later sign-in with the same principal and role sees the interactive answer and its saved state, as it sees the message's tables and charts. Optimistic revisions reject stale writes. Action operation IDs are idempotent; the server reloads and validates the source, declared action, state, current agent/thread access and security context. Form content is untrusted user input. Navigation flushes pending state; a failed save leaves the user in the current context. Assistant form submission sends values through Nova to its configured model provider; local filters and calculators do not issue model requests.

Interactive UI generation and its state/action APIs are always on. Unknown catalog versions and malformed programs also fall back. HTML/canvas runs in an offline sandbox with no business evidence and no parent access.

## One Studio renderer

Studio has one visual system: the intelligent UI catalog components (`Chart`, `Table`, `GeoMap`, `Kpi` and the rest). No surface uses a second chart or table renderer.

- **Model programs.** `presentation: "interactive"` asks the model for a composed view: calculators, forms, simulations, local filters, dashboards that combine results, diagrams, route maps, flashcards, timers and canvases. The catalog remains available when the planner prefers text and joins the pinned system prompt; context curation trims history and stale tool payloads instead, so the prompt is never dropped to save tokens. The provider's own context window still ends a turn through `context_overflow`.
- **Deterministic blocks.** When an answer has no model program, `assistant/ui_autocompose.py` adds one block for its displayed verified results without a provider call: a governed `GeoMap` for the first result with latitude/longitude columns (or WKT/GeoJSON geometry), even beside a tool chart since a chart does not show locations; otherwise, when no tool chart is shown, up to four `Kpi` tiles for single-metric results. These blocks pass the same Node validator, bind `@Evidence`, carry `provenance.composer = "deterministic"` and follow the same security-context visibility as model blocks.
- **Stored charts.** `data_to_chart`, saved artifacts and dashboard tiles keep Vega-Lite v5 as their stored data template. The frontend adapter `intelligent-ui/chart-spec-block.ts` turns a spec and its rows into a catalog program for the same renderer: bar/line/area/arc/point marks, nominal colour through `@Pivot`, quantitative colour as a second series, descending category sort through `@Sort`, and `count` aggregates counted from the rows. Donut marks retain their inner radius. Unsupported transforms, time buckets, bins and extra grouping fall back to a `Table` of the original rows rather than silently changing the plotted values. Vega is no longer a frontend dependency.
- **Tables.** `result-table.tsx` is the only result grid: answer tables, catalog `Table` nodes, chart companion data, artifacts, dashboard tiles and shared results.
- **Saving.** Charts, tables and maps inside a block save as artifacts. Every block records `provenance.tool_calls` (evidence id → query tool call), so the artifact reuses that query's read-only SQL. Missing query provenance refuses saving instead of guessing a nearby query. Distinct chart definitions keep independent saved states. A chart of the result's own columns (one measure, or two with the second as colour) is saved as a Vega-Lite template; a chart of values calculated in the answer is saved as its source table, and the reader is told so.

Smart is the default for a new chat. The brain toggle exposes `aria-pressed`; the manual agent selector appears only when Smart is off. Draft and attachments survive mode changes, and the last accessible manual selection is restored. A pending manual selection blocks sending. Mode changes are disabled during a run so its identity stays stable. The existing composer dimensions and styling remain intact.

Activity is summarized in one disclosure. Tools, evidence and child work remain inspectable; consent and actionable failures remain visible. Answers use the normal transcript width and prose spacing. Controls can be grouped, without wrapping each answer in a second card. Completed interactive answers group raw query tables in one source-data disclosure; filtering, export and artifact actions remain available. Unknown catalog versions and failed turns keep source tables expanded.

## Charts and geospatial

Charts support bar, line, area, pie/donut and scatter, tooltips, series selection, a companion table, local result filtering and declared follow-up actions. Brush zoom is available where the data shape makes it meaningful. Chart/map code is loaded lazily.

GeoMap accepts longitude/latitude columns or validated WKT/GeoJSON Point, LineString and Polygon. Coordinates must be finite, two-dimensional and within longitude ±180 / latitude ±90. Rings must be closed with at least four positions. The renderer does not guess locations or swap coordinates. SQL DECIMAL coordinate columns may use strictly formatted decimal strings; booleans, blank strings, exponent/hex/comma formats and out-of-range values are rejected. GeoJSON positions require numeric values. It caps display at 1,000 rows and 20,000 positions; invalid and omitted counts remain visible, with invalid rows identified in the table.

Map actions resolve the selected row on the server by evaluating the scoped source program with the submitted filters. Selection is zero-based within the displayed rows; absent, out-of-range, fractional and invalid-geometry selections yield no row. Random map transforms are refused because they cannot preserve selection across runtimes. Up to ten map selections and 16 KiB of selected-row context are allowed. The model receives the selected row snapshot instead of internal selection indices. This snapshot cannot authorize tools or replace fresh evidence when a new query is needed.

MapLibre renders point clusters, lines and polygon outlines/fills. Selection is declarative and linked to the location table. The table remains usable if WebGL fails. Radius and containment results must originate in Semantic Views or configured SQL tools through Nova's SQL frontend. Tests use the pinned StarRocks 4.1.4 functions; browser geometry rendering does not establish analytical containment or distance evidence.

Business basemap: bundled Natural Earth 1:110m land geometry, public domain. Attribution is visible; licensing/source information is in `frontend/public/licenses/natural-earth.txt`. It is an overview, not a street map. No business coordinates, labels or viewport go to external tile, routing, glyph or geocoding services. Public Map has a separate, explicitly public adapter and preserves location text on network failure.

## Runtime harness changes

Nova already owns bounded inference, tool authorization, consent, role revalidation, evidence, delegation, cancellation and journaled recovery. The changes extend those owners:

- Checkpoints persist remaining turn time, an absolute deadline and consumed measured tokens, alongside existing repairs, tool uses, evidence and operation identities. Downtime cannot replenish the allowance. A budgeted recovery without a valid token counter fails closed before another provider call.
- Provider retries, tool repairs and UI repair share six repair slots, with stricter per-kind limits. Existing terminal authorization/policy/consent failures do not become retryable. Legacy counts consume the allowance; malformed counts fail closed.
- Smart root projection preserves sealed UI and ordered prose. Terminal fragments receive distinct replay cursors. Smart cursors are decimal strings because timestamp-based cursor expansion exceeds JavaScript safe integers; clients compare with `BigInt`. Legacy safe numeric cursors remain readable.
- Run-row writes share a renewable Redis lock across API processes, workers and the direct journal. StarRocks updates rewrite whole rows, so a process-local lock cannot prevent a concurrent heartbeat from restoring a cancelled state. Cancellation shares root admission coordination with delegation and serializes each descendant update. A missing or lost coordinator fails closed; an uncertain write is not automatically replayed.
- Smart run observation, cancellation and SSE replay compare owner, active role and security-context version; a missing legacy version fails closed.
- Existing generation/lease fencing and uncertain mutation replay guards remain in the shared engine. UI does not execute a parallel inference loop.
- Direct-agent reload discovers the running journal only for the same principal, role and security-context version, then subscribes without reposting the message. Stop requests cancellation through Redis and waits for the server's terminal event. A disconnected browser alone does not cancel work. Persisted terminal outcomes preserve stopped/error labels after history reload, including cancellation during journal writes. A Smart Stop clicked before run acceptance is retained until its run ID arrives; failed cancellation leaves the stream available for retry.
- A StarRocks planner timeout while polling worker metadata uses bounded exponential backoff, retaining active tasks. Other syntax/access failures remain terminal. This retry does not replay tools or uncertain mutations.

## Offline experiment contract

`POST /api/v1/agents/{agent_id}/quality/harness-experiments` accepts an operation ID, baseline version, distinct case IDs, and explicit time/token ceilings. The default is one cycle, 600 seconds and 48,000 tokens; request bounds are enforced. It uses the existing quality runs metadata/storage path and requires Redis coordination.

Token accounting includes measured planner and response calls. Missing usage stops a budgeted evaluation; exhausted budgets prevent another loop iteration or experiment stage. A final provider response may cross the remaining allowance; its observed usage is retained and the experiment falls back to baseline. This is a measured execution budget, not a provider billing cap.

Each stage records its in-flight marker before execution and its result afterward. Completed stages are reused on resume. An interrupted stage with an uncertain result is marked unavailable and requires a new operation; it is not silently rerun. Redis lease renewal fences checkpoints. Changing inputs under the same operation ID returns a conflict.

Candidates can append validated response instructions. They cannot change tools, model/provider configuration, resources, policies, credentials or executable capabilities. Proposals use `AssistantLoop` with an empty tool registry; probes use the existing read-only evaluation registry and release pin validation. Composition sees scorer outcomes, not private grading answers.

Selection requires the same case/scorer set, a complete eligible candidate, no loss of a passing assertion, and strictly more passes. Ties, invalid proposals, uncertain stages and incomplete runs retain baseline. The recommendation is an immutable candidate version; ordinary draft/version/release approval gates still govern promotion.

UI validity, evidence binding and source-validated action completion extend quality facts/scorers (`nova-agent-scorers:2`). A cancelled, interrupted or failed action cannot score as completed. Smart completion retains final response usage that arrives after the last checkpoint, without counting checkpointed usage twice. The shared engine records first-useful-content latency with bounded direct/Smart labels. UI validation, repair and fallback use the existing bounded Studio operation metrics; existing run traces retain usage, latency and recovery outcomes. Metrics labels contain no programs, coordinates, state values or credentials.
