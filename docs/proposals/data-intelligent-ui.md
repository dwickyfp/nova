# Data-first intelligent UI

**Status:** Phase 0 is recorded in section 7, and the product owner approved
`VizView` as the single chart renderer on 6 Nov 2026. Phase 1 (data-first
composition with the existing components) is implemented; see "Phase 1
results". Phases 2–5 are not started.
**Branch checked:** `feat/supernova-ontology` @ `803f2902` (6 Nov 2026).
**Related:** [Studio interactive answers](../architecture/studio-intelligent-ui-v2.md),
[Studio mapcn maps](../architecture/studio-mapcn.md),
[Arch 16: viz core](../arch-16-viz-core.md),
[Arch 11: Smart collaboration](../arch-11-smart-collaboration.md),
[Arch 18: business ontology](../arch-18-business-ontology.md).

Nova's intelligent UI started from
[Open Intelligent UI](https://github.com/thesysdev/open-intelligent-ui), a
component catalog built for a general assistant. It already runs in Studio,
binds business values to verified evidence, and travels on the one Studio
answer stream. What it does not do yet is treat data as the default subject. A
question about outlets, routes or revenue gets Markdown unless the planner
decides the turn is "interactive", and the catalog the model reads gives as
much space to flashcards and YouTube as to grids and comparisons.

This proposal turns the same intelligent UI into a data-first answer surface.
It keeps the existing catalog, validator, evidence binding and stream. It adds
data components, makes them the default for data answers, and links them to
governed drill-down and to the business ontology.

Two constraints from the product owner hold throughout:

1. The intelligent UI stays. No component, validator or renderer is removed to
   make room for this work.
2. There is exactly one stream and render path for answer content. New
   components are new catalog entries inside the existing `ui` block, not new
   event types or a second renderer pipeline.

---

## 1. Current state

### 1.1 Composition (backend)

| Piece | Where | What it does today |
| --- | --- | --- |
| Program extraction | [`intelligent_ui.py:34`](../../backend/app/modules/assistant/intelligent_ui.py) `split_program` | Takes the one ` ```openui ` program out of the model's Markdown, including bare or unfenced programs, so it is validated rather than shown as text. |
| Composer prompt | [`intelligent_ui.py:92`](../../backend/app/modules/assistant/intelligent_ui.py) `composer_prompt` | Injects the catalog prompt. When the planner chose `text`, it wraps the catalog in `<OPTIONAL_UI_CATALOG>` and tells the model that plain Markdown is a complete answer. |
| Validation | [`intelligent_ui.py:187`](../../backend/app/modules/assistant/intelligent_ui.py) `parse_program` | Runs the generated Node validator (`ui_runtime/validator.cjs`) in a 5 s, 64 MB subprocess, at most four at a time. Evidence passed in is capped by `_records` at 200 rows and 50 columns per result, with credential columns and values dropped. |
| Model composer | [`intelligent_ui.py:241`](../../backend/app/modules/assistant/intelligent_ui.py) `compose_block` | Requires a Markdown fallback, validates the program, and for business turns runs `check_numeric_answer` on the displayed text so the UI cannot show numbers the evidence does not support. |
| Stored block | [`intelligent_ui.py:284`](../../backend/app/modules/assistant/intelligent_ui.py) `block_dict` | Program, SHA-256, fallback, state defaults, actions, bound evidence rows and provenance (`evidence_ids`, `scope`, `omitted_rows`, `composer`). |
| Deterministic composer | [`ui_autocompose.py:154`](../../backend/app/modules/assistant/ui_autocompose.py) `compose` | When the model wrote no program, draws a GeoMap for a located result or up to four KPI tiles for one-row, one-column results. Nothing else. Called from [`service.py:4234`](../../backend/app/modules/assistant/service.py) `_autocompose_artifacts`. |
| Planner | [`planning.py:116`](../../backend/app/modules/assistant/planning.py) | `presentation: interactive` is described as "a calculator, checklist, form, simulation, local data filter, dashboard combining results, diagram, route/itinerary map, flashcards, timer or canvas". Otherwise `text`. |
| Catalog | `backend/app/modules/assistant/ui_runtime/catalog.json` (`nova-openui-3`), generated with the frontend validator by `frontend/scripts/build-ui-validator.mjs` | 57 components. `nova-openui-2` is still accepted for replay (`SUPPORTED_CATALOGS`). |

Who gets the composer: single-agent Studio turns
([`router.py:2860`](../../backend/app/modules/agents/router.py), every agent
except the skill author) and Smart roots
([`harness_worker.py:1250`](../../backend/app/modules/agents/harness_worker.py),
`intelligent_ui=is_root and smart`). Specialists do not compose UI; the Smart
root composes once over their rows.

### 1.2 The stream

There is already one stream for answer content. A sealed UI block is sent as
three SSE frames and stored as one `kind: "ui"` step:

```
ui                  {content_index, content_id, payload: block without program}
ui_delta (n times)  {content_index, content_id, text: next 4096 chars of program}
content_block_done  {content_index, content_id}
```

- Single-agent turns emit them in
  [`service.py:3867`](../../backend/app/modules/assistant/service.py).
- Smart roots emit the same frames from sealed artifacts in
  [`router.py:511`](../../backend/app/modules/agents/router.py)
  `_smart_final_frames`.
- Studio reduces them in
  [`studio-transcript.ts:822`](../../frontend/src/features/studio/studio-transcript.ts)
  (live) and restores them from stored steps at
  [`studio-transcript.ts:228`](../../frontend/src/features/studio/studio-transcript.ts).

The same stream also carries `table` and `chart` frames from query tools and
`data_to_chart`, plus `text`, `citation`, `dashboard`, `automation_proposal`
and `smart_draft`. All of them land in the turn's ordered `content` list.

UI actions (a button with `@ToAssistant`, a map selection) go back through the
normal message endpoint as `ui_action`
([`router.py:2610`](../../backend/app/modules/agents/router.py)). Nova
re-validates the stored program against its stored evidence and resolves
`selected_rows_snapshot` itself in
[`intelligent_ui.py:362`](../../backend/app/modules/assistant/intelligent_ui.py)
`action_context`; client-supplied rows are never trusted. The follow-up is an
ordinary turn on the same stream.

### 1.3 Rendering (frontend)

| Piece | Where |
| --- | --- |
| Ordered answer content | [`turn-content.tsx`](../../frontend/src/features/studio/turn-content.tsx): text, `ui`, `table`, `chart`, `dashboard`, `citation`. When a v3 `ui` block completes, the turn's source tables fold under "Source data". |
| UI renderer | [`renderer.tsx:46`](../../frontend/src/features/studio/intelligent-ui/renderer.tsx) `IntelligentUi`, with components in [`components.tsx`](../../frontend/src/features/studio/intelligent-ui/components.tsx) |
| Table node | `DataTable` in `components.tsx` wraps the Studio [`ResultTable`](../../frontend/src/features/studio/result-table.tsx), so query results and UI tables already look alike. |
| Chart node | [`data-chart.tsx`](../../frontend/src/features/studio/intelligent-ui/data-chart.tsx), Recharts, with series toggles and a "show data" table. |
| Tool chart | [`result-cards.tsx`](../../frontend/src/features/studio/result-cards.tsx) `ResultChart`: a viz-core `presmodel` renders through `VizView` (no headline or highlight is passed inline; `ViewAnswer` with `describeView` from `insight.ts` is used only on saved artifact pages, [`studio-artifacts.tsx:843`](../../frontend/src/features/studio/studio-artifacts.tsx)); otherwise `UiChart` converts the Vega-Lite `chart_spec` into a UI block ([`chart-spec-block.ts`](../../frontend/src/features/studio/intelligent-ui/chart-spec-block.ts)) and draws it with the Chart node. |
| Business map | [`geo-map.tsx`](../../frontend/src/features/studio/intelligent-ui/geo-map.tsx), [`map-surface.tsx`](../../frontend/src/features/studio/intelligent-ui/map-surface.tsx); legacy renderer in `geo-map-legacy.tsx`. |

### 1.4 What the catalog contains

Of the 57 components in `nova-openui-3`, four bind business evidence:

| Data-bound | Layout and text | Input | General-purpose |
| --- | --- | --- | --- |
| `Kpi`, `Table`, `Chart`, `GeoMap` | `Col`, `Row`, `Box`, `Grid`, `Card`, `Carousel`, `Popover`, `Divider`, `Spacer`, `Title`, `Text`, `Caption`, `Label`, `Badge`, `Link`, `List`, `ListItem`, `Markdown`, `Quote`, `Code`, `Math`, `Icon` | `Button`, `IconButton`, `Pressable`, `Input`, `TextArea`, `Select`, `SegmentedControl`, `RadioGroup`, `Slider`, `Checkbox`, `DatePicker`, `Form` | `Timer`, `SearchImage`, `Image`, `Map`, `MapMarker`, `YouTube`, `Flashcard`, `Celebration`, `HtmlView`, `Svg` and nine SVG shape primitives |

The function list is stronger than the component list. It already has data
transforms that are labelled "within these result rows": `@Filter`, `@Sort`,
`@Top`, `@Between`, `@GroupBy`, `@Pivot`, `@Stats`, `@Histogram`, `@Change`,
`@Cumulative`, `@MovingAvg`, `@Percent`. It also has `@Random`, `@Shuffle`,
`@Pick`, `@Payment` and the date helpers a general assistant needs.

The governance layer is solid and is the reason to build on this rather than
beside it: business visuals must bind `@Evidence(id)`, values cannot be
transcribed, displayed numbers are checked, state is bounded and
secret-filtered, HTML and public media cannot carry business data, and actions
resolve selections on the server.

---

## 2. Gaps against a data-first experience

**G1. Data is opt-in.** The planner defaults to `text`, its description of
`interactive` is mostly non-data widgets, and the composer prompt says plain
Markdown is complete. A data question gets a component only when a query tool
also produced a chart, or when the deterministic composer finds coordinates or
a single metric.

**G2. The catalog reads like a chatbot's.** The model sees four data components
among 57, and the examples in the prompt spend as much space on public places,
assumption calculators and follow-up forms as on evidence. The model picks
from what it is shown.

**G3. Missing data-native components.** There is no comparison or variance
view, no ranking with share of total, no pivot or crosstab, no distribution
view, no shared filter bar, no period-over-period trend, no definition or
freshness note on a metric. Several of these exist as functions (`@Pivot`,
`@Histogram`, `@Change`) with no component that renders them well.

**G4. Existing data components are thin.** `Kpi` shows one value from the first
row with a label and digit count: no comparison, unit, direction or metric
definition. No inline chart has an insight headline or highlighted mark,
although the viz core computes both (`insight.ts`) and shows them on saved
artifact pages. A block binds at most 200 rows per result; the block footer
says how many rows were omitted and suggests asking Nova to narrow the query,
but offers no action that fetches the rest.

**G5. Two chart renderers in one answer.** A tool chart with a `presmodel`
renders through `VizView`; a chart inside a UI block renders through
`DataChart`. Both draw with Recharts, but each has its own geometry, palette,
legend and interaction code, so one answer can show two charts that look and
behave differently. `ResultTable` is already shared, so tables do not have this
problem.

**G6. Drill-down is free text.** Selection works (GeoMap `$selected`, Chart
`selected`) and Nova resolves the selected rows server-side, but the next step
is `@ToAssistant("Investigate the selected result row")`. The planner gets
prose, not "break revenue for outlet X down by month". Filters cannot be
pushed back into a governed query; they only narrow the 200 rows in the block.

**G7. Labels come from column names.** `ui_autocompose._label` turns
`total_revenue` into "Total revenue". The ontology already holds metric names,
definitions and units under a release
([Arch 18](../arch-18-business-ontology.md)), and Studio is ontology-aware for
grounding, but the UI does not read that vocabulary.

**G8. Local aggregates over capped rows can mislead.** A `@Sum` over the 200
bound rows of a 5,000-row result is labelled "within these result rows" in the
prompt rules. The renderer adds a block-level footer when `omitted_rows > 0`,
but the component that shows the total does not mark it as partial, and rows
the engine already capped before Nova bound them are not counted at all.

Documentation and configuration debt found while checking (not in scope here):

- `VITE_STUDIO_MAPCN_ENABLED`
  ([`map-flag.ts`](../../frontend/src/features/studio/intelligent-ui/map-flag.ts),
  documented in [studio-mapcn.md](../architecture/studio-mapcn.md)) is a
  feature flag of the kind `AGENTS.md` now disallows. Removing it is a
  separate change.
- [Arch 16](../arch-16-viz-core.md) describes `VizView` as the inline chat
  chart renderer; charts without a `presmodel` and every chart inside a UI
  block render through Recharts instead.

---

## 3. Goals

1. **A data answer shows data.** When a turn has verified business evidence,
   the answer includes at least one evidence-bound component, whether or not
   the planner chose `interactive` and whether or not the model wrote a
   program.
2. **The catalog leads with data.** The composer prompt presents data
   components, data functions and data examples first. General components
   remain available and documented after them.
3. **Every data component is governed the same way.** Values bind
   `@Evidence(id)` or a local transform of it, displayed numbers pass the
   numeric check, partial results say so in the component, and no business
   value reaches HTML, SVG primitives, public media or URLs.
4. **Interaction leads to governed queries.** Filters, selections and
   drill-downs either narrow bound rows locally (and say so) or start a new
   turn that runs a new query under the caller's principal and active role.
5. **One look per visual type.** A chart looks and behaves the same whether a
   tool, the deterministic composer or the model produced it.
6. **Business vocabulary.** When the ontology release in scope defines a
   metric or concept, components use its name, unit and definition.

## 4. Non-goals

- Deleting the intelligent UI, any catalog component, the validator, or
  `nova-openui-2`/`-3` replay.
- A second SSE path, a second set of answer event types, or a parallel
  renderer for "data answers".
- Client-side SQL, client-side access to unbound data, or components that
  fetch rows on their own.
- BI authoring. Saved charts and dashboards remain Superset objects
  ([Arch 17](../arch-17-superset.md)); the UI only offers the existing save.
- A feature flag, rollout gate or opt-in mode for the new behavior.
- Changes to authentication, Ranger policy, row filters or masks.
- Changing how Smart delegates. Specialists keep returning rows; the root
  keeps composing.

---

## 5. Target component catalog

Principles:

- Extend existing components with optional trailing arguments before adding
  new ones. Positional arguments are how openui-lang works, so additions go at
  the end and old programs keep validating.
- A new component is justified when its layout, accessibility equivalent or
  governance check differs from what an existing one can express.
- Every data component renders an accessible data equivalent (the Chart node
  already does) and a short provenance line.

### 5.1 Data components

| Component | Status | Purpose | Binds to | Interactions | Governance notes |
| --- | --- | --- | --- | --- | --- |
| `Table` → data grid | Extend | Rows with typed columns, number and date formats, sort, local search, column choice | `@Evidence(id)` or a local transform | Sort, filter, row select (`$selected`), save as artifact (exists), "get all rows" drill when `omitted_rows > 0` | Shows "Showing 200 of N rows" from `omitted_rows`. Formats are display-only. |
| `Kpi` | Extend | One metric, optionally against a comparison value | Value: evidence row/column. Comparison: a second column or a second evidence id | Popover with definition and source | Comparison must also be bound. Direction (up is good / bad) comes from the ontology metric, never guessed. |
| `Chart` | Extend | Bar, line, area, pie, donut, scatter; add `histogram` and an optional comparison series | `@Evidence(id)`, `@Pivot`, `@Histogram` | Series toggle and data view (exist), mark select, drill on mark | Headline from the `insight.ts` computation over the rendered rows, not model text. Highlighted mark matches the headline. |
| `Comparison` | New | Two periods, segments or entities side by side with absolute and percentage change | Two evidence ids with matching keys, or one result with two value columns | Sort by change, select a row | `@Change` semantics: null when the base is zero. Keys that do not match are listed, not dropped silently. |
| `Ranking` | New | Top or bottom N with share of total and rank | `@Evidence(id)` sorted by a numeric field | Select an item, switch top/bottom | Share of total is labelled partial when rows were omitted. |
| `PivotGrid` | New | Crosstab of a measure by two dimensions, with row and column totals | `@Pivot` over evidence | Sort by a column, select a cell | Totals are local sums and say so when partial. |
| `Trend` | Chart option, not a new component | Time series with period-over-period delta and optional moving average | Evidence with a temporal column | Range brush (the Chart node already shows one above 12 points) | Uses `@MovingAvg`/`@Change` on bound rows only. |
| `GeoMap` | Keep | Located results, routes, origin–destination arcs | Evidence with coordinates or WKT/GeoJSON | Selection, viewport save (exist) | No change. Choropleth only when the query supplies polygons. |
| `FilterBar` | New | Shared filters over every component bound to the same evidence id | Declared `$filter_*` state; options from distinct values in the bound rows | Local narrowing; "Run with this filter" drill | Local mode is labelled "within these rows". Pushing a filter into a query is a drill (5.2). |
| `EvidenceNote` | New, also rendered automatically under each data component | Source and limits of what is shown | Block provenance: evidence id, tool call or statement, row count, omitted rows, ontology release | Open the source query in the transcript trace | No SQL text or credential is rendered; it links to the existing tool step. |
| `MetricDefinition` | New | The ontology definition, unit, owner and release of a metric | Ontology metric binding resolved at compose time | Popover | Read-only. Shows nothing when the metric is not in the released ontology. |

### 5.2 Drill-down as a typed action

Add one action step next to `@ToAssistant`, `@Set`, `@Reset` and `@OpenUrl`:

```
@Drill(kind, field, value?)    kind: "breakdown" | "filter" | "detail" | "all_rows"
```

- It does not run anything in the browser. The click goes through the existing
  `ui_action` request, with the action id, as today.
- On the server, `action_context` already resolves the selected rows from
  stored evidence. The drill adds a structured intent next to them: which
  evidence id, which field, which kind. The follow-up is a normal turn in the
  same thread, so it runs through the shared bounded engine, the planner, the
  caller's principal and active role, Ranger row filters and masks, and audit.
- `field` must be a column of the bound evidence or a dimension the ontology
  links to it. Anything else fails validation and is audited as a refusal.
- A drill cannot broaden access. It can only ask for a query the caller could
  have asked for in words.

### 5.3 General components

`Timer`, `Flashcard`, `Celebration`, `YouTube`, `SearchImage`, `Image`, `Map`,
`MapMarker`, `HtmlView`, `Math`, `Quote` and the SVG primitives stay in the
catalog and the validator. They move to a second section of the composer
prompt, "General components", after the data section. Assumption calculators
and simulations keep working and keep their "assumption" provenance.

---

## 6. How the single stream carries this

No new SSE event type, no new step kind, no new renderer entry point.

1. **Catalog version.** The new components and arguments ship as
   `nova-openui-4`, generated with the validator by the existing build script.
   `SUPPORTED_CATALOGS` adds it and keeps `-2` and `-3`, so stored turns replay
   unchanged. The frontend catalog file follows the same pattern as
   `catalog-v2.ts`.
2. **Blocks.** New components live inside the `program` of a `ui` block. The
   block shape from `block_dict` gains only optional provenance fields
   (ontology release and metric refs per evidence id, per-evidence total row
   counts). Stored steps without them still render.
3. **Frames.** `ui`, `ui_delta`, `content_block_done` as today, from
   `service.py` for single-agent turns and `_smart_final_frames` for Smart
   roots. The program stays under `MAX_PROGRAM_BYTES` (64 KB); evidence stays
   under the 512 KB validator payload.
4. **Default composition.** `_autocompose_artifacts` already appends a
   deterministic `ui` block when the model wrote none. Its `compose` grows from
   map-or-KPI to a small decision table: located rows → GeoMap; one row of
   metrics → Kpi tiles; a temporal column and a measure → Trend chart; one
   dimension and one measure → Ranking or bar Chart; two dimensions and a
   measure → PivotGrid; otherwise → data grid. It stays deterministic, makes
   no provider call and runs the same validator.
5. **Tool charts and tables.** `table` and `chart` frames stay, because saved
   artifacts, Superset conversion and replay depend on them. They render
   through the same component implementations as UI nodes: `ResultTable`
   already does; charts converge in Phase 5.
6. **Actions.** `@Drill` uses the existing `ui_action` request and produces a
   normal turn. Nothing streams outside the turn.
7. **Smart.** Specialists return rows, the root composes once, and the sealed
   block replays through `_smart_final_frames` with the same catalog.

---

## 7. Phased plan

Each phase ends with the checks `AGENTS.md` routes for its change: backend
unit suite with coverage, agent eval scorecard and changed-file Ruff for
backend; `pnpm lint`, `pnpm build`, `pnpm test:coverage` and browser checks for
frontend; affected `tests/eval/` trajectories for agent behavior. The live
field-sales dataset (outlets, routes, sales) is the acceptance set for the
"live" criteria below.

### Phase 0 — Baseline and decisions

- Measure, from stored steps on the field-sales dataset, how many data turns
  end with an evidence-bound component today, split by composer (`model`,
  `deterministic`, none) and by single-agent vs Smart.
- Decide the chart renderer for Phase 5 (see open question 1).
- Write the decision table for deterministic composition (6.4) as test cases.

**Done when:** a baseline number is recorded in this document, and the
renderer decision and test cases are approved.

#### Phase 0 results (6 Nov 2026)

**Baseline.** Measured from `NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES` on the
local stack, read-only. The table holds 34 assistant turns, all by
`nova_admin`, from 04:24 to 12:03 on 6 Nov 2026. Older history is not in the
table, so this is one day of the live field-sales test, not a long-run rate.

| Turns | Count | With an intelligent UI block | With a visible table | With a tool chart | With no data visual |
| --- | --- | --- | --- | --- | --- |
| Single-agent, verified rows (`semantic_query`) | 2 | 0 | 2 | 0 | 0 |
| Smart root, verified rows | 6 | 0 | 0 | 2 | 4 |
| **All data turns** | **8** | **0** | 2 | 2 | **4** |
| Other turns (errors, clarifications, ontology and knowledge steps, greetings) | 26 | 0 | — | — | — |

A Smart root keeps its specialists' newest rows as a `result` step
([`harness_worker.py`](../../backend/app/modules/agents/harness_worker.py)
`_carried_result`) so the next turn can refer to them. That step is not
displayed, so four Smart answers showed prose only.

No turn recorded a `ui` step, a `ui_repair` or a `ui_fallback`, so the model
never attempted a program and the validator never rejected one. Every data
result had the same shape: one dimension and one measure, for example
`region` × `revenue_total` (5 rows) or `channel` × `revenue_total` (3 rows).
The deterministic composer skips that shape, because it only draws a map for
located rows or KPI tiles for a one-row, one-column result. The two charts
came from `data_to_chart` with a `presmodel`, so they rendered through
`VizView`.

The baseline is therefore **0 of 8 data turns with a data component from the
intelligent UI**. In the Phase 1 decision table, all eight shapes map to a
Ranking or bar Chart.

**Chart renderer.** Both renderers already use Recharts, so this decision is
about which presentation layer survives, not which library.

| | `VizView` ([`viz-view.tsx`](../../frontend/src/features/viz/viz-view.tsx)) | `DataChart` ([`data-chart.tsx`](../../frontend/src/features/studio/intelligent-ui/data-chart.tsx)) |
| --- | --- | --- |
| Input | `PresModel`: classified columns, axes, legend, totals, built by `viz/presmodel.py` | Plain records plus `x`, `y`, `type`, `series` |
| Reacts to local filters | No; the presentation model is fixed when the server builds it | Yes; it re-renders from filtered records |
| Marks | bar, line, area, circle, square, tick, pie, text crosstab | bar, line, area, pie, donut, scatter |
| Multiple measures | One pane per measure, each with its own axis | Series on one axis |
| Highlight | `highlight` prop drawn in `--viz-highlight` | None |
| Selection | `onMarkClick` with dimension values | `$selected` state binding, which the server resolves for actions |
| Data equivalent | `TextTable` and `FallbackGrid` | "Show data" table through `DataTable` |
| Extras | Thumbnail mode, used by Explore and legacy dashboards | Series toggles, brush above 12 points, per-node save |

Recommendation: keep **`VizView` as the one chart presentation**. Render the
UI `Chart` node through it with a small browser-side adapter that turns
records, `x`, `y` and `series` into a `PresModel` on every render.
`PresModel` is plain data (columns, rows, axes, legend, totals), and evidence
rows in a UI block are already aggregated, so the adapter only classifies and
sums. It does not compute a query. This keeps local filters reactive, gives UI
charts the multi-measure panes and highlight, and leaves `viz/presmodel.py` as
the reference for field classification. The catalog signature of `Chart` does
not change. `DataChart`'s state binding, series toggles, brush, "show data"
and save move into `VizView` or a thin wrapper around it; donut maps to pie
with an inner radius, and scatter maps to circle.

**Decision (6 Nov 2026):** the product owner approved `VizView` as the single
chart renderer, with the browser-side adapter described above. A screenshot spike on the live
field-sales charts was not run, because the shared dev stack belongs to the
live test session; the comparison above comes from code. The first Phase 1
task is to build the adapter for the two field-sales shapes above and compare
screenshots in light and dark.

**Decision table for deterministic composition.** These are the test cases
for `ui_autocompose.compose` in Phase 1. Shapes come from the field-sales
gold answers (`~/.cache/nova-field-lab/gold.json`) and today's stored turns.
The rule applies only when the model wrote no program and no other visual
already shows the same evidence. A tool chart still suppresses everything
except the map, as today.

| # | Verified result shape | Example | Expected block |
| --- | --- | --- | --- |
| 1 | Rows with latitude/longitude or WKT/GeoJSON | outlets with coordinates; a route LineString | `GeoMap` (unchanged) |
| 2 | One row, one numeric column | total sales for September 2026 | One `Kpi` (unchanged) |
| 3 | Several one-row numeric results, or one row whose columns are all measures, up to four tiles | total sales and total orders for one month; visits and km for one day | `Kpi` grid |
| 4 | One temporal column, one measure, at least three rows | weekly sales from 2026-04-06 to 2026-10-12 (28 rows) | Line `Chart` (Trend) |
| 5 | One categorical column, one measure, 2 to 30 rows | `region` × `revenue_total` (5); sales by salesperson (6) | `Ranking`, or a bar `Chart` sorted by the measure until `Ranking` ships |
| 6 | One categorical column, one measure, more than 30 rows | sales by outlet | Top 10 bar `Chart` plus a data grid of all bound rows, labelled "within these result rows" |
| 7 | Two categorical columns and one measure | sales by region × channel | `PivotGrid`, or a data grid until it ships |
| 8 | Other mixes of categories, time or keys with up to four measures, at least two rows | region × revenue, orders, outlets; month × region × revenue | Data grid; a chart only when the measures share a unit |
| 9 | Ordered list without a measure | Budi's visit order for 2026-10-12 | Data grid in the stored order, without sorting |
| 10 | One row that is not all measures, more than four measures, or more than 50 columns | — | No block; the folded "Source data" table is the answer's data view |
| 11 | No rows | — | No block |
| 12 | Credential-like column or value | — | No block for that result (existing `_records` rule) |

Every case also asserts that the block validates, binds `@Evidence(id)`, has
`provenance.composer == "deterministic"`, and adds no SSE event type.

### Phase 1 — Data first, with the components that exist

- Rewrite the catalog prompt order: data components, data functions, data
  examples, then general components. Replace two of the five examples with a
  ranking and a trend over evidence.
- Change the `presentation` description in `planning.py` so a governed data
  view counts as interactive, and so `text` turns with evidence still get the
  deterministic block.
- Extend `ui_autocompose.compose` with the decision table, using today's
  `Table`, `Chart`, `Kpi` and `GeoMap`.
- Render the partial-rows notice from `omitted_rows` under Table and Chart.
  (Found during Phase 1: the renderer already shows a block-level notice;
  per-component marking moves to Phase 2.)

**Acceptance:**

- Every turn in the field-sales eval set that returns verified business rows
  shows at least one evidence-bound component, live and on replay, for both
  single-agent and Smart roots.
- Greetings, explanations and clarifications still render as Markdown only.
- A test asserts the set of SSE event types is unchanged.
- Stored v2 and v3 turns render as before (existing screenshot tests).
- Numeric-check failures and validator failures still fall back to Markdown
  and are recorded as `runtime_decision` steps.

#### Phase 1 results (6 Nov 2026)

Implemented on branch `feat/data-first-ui`:

- [`ui_autocompose.py`](../../backend/app/modules/assistant/ui_autocompose.py)
  classifies each verified result's columns as temporal, measure, key or
  category (by values first, then by name for numeric periods such as `year`
  and identifiers such as `visit_order`) and applies the decision table
  above: trend, ranking (sorted bar, or top ten plus a grid above 30 rows),
  grid, and KPI tiles for a one-row result of measures. A located result still
  gets only its map, a tool chart still leaves only maps, and at most two
  results besides KPIs get a view. Each view is validated by the same Node
  runtime and recorded with `provenance.auto` (`trend`, `ranking`, `grid`).
- The catalog lists `Kpi`, `Table`, `Chart` and `GeoMap` first. The preamble
  says Nova is a data platform; two new rules say how to pick a data component
  from the result's shape and keep general components off data answers. The
  examples now start with a ranking, a trend and a filtered detail; the
  follow-up form example was removed and the public-places example moved
  last. The catalog stays `nova-openui-3`: components, arguments and
  validation rules are unchanged, so stored blocks replay as before; only the
  prompt text and the bundle hash changed.
- The optional-catalog wrapper in `composer_prompt` and the planner's
  `presentation` description now name data views first.
- No new SSE event, step kind or renderer. A trajectory test checks that a
  ranking travels on the same frames a map already uses and adds no provider
  call.

Not yet measured live: whether every data turn on the field-sales set now
shows a component. That needs the live run after this branch merges, and the
Smart root path in particular (its `result` rows reach the composer through
the imported specialist evidence) should be checked there.

### Phase 2 — Richer data components

- Ship `nova-openui-4` with the extended `Table`, `Kpi`, `Chart`
  (insight headline, histogram, comparison series, trend options) and the new
  `Comparison`, `Ranking`, `PivotGrid`, `EvidenceNote`.
- Validator rules: comparison values must be bound; partial aggregates must be
  marked; no business value in general components.

**Acceptance:**

- Validator tests reject transcribed values, unbound comparisons and business
  values in general components; they accept every example in the prompt.
- Each new component has light and dark screenshot tests and an accessible
  data equivalent, and passes the layout checks at mobile width.
- The headline text of a chart is computed from rendered rows and matches the
  highlighted mark.
- Old programs validate under v4 without changes.

### Phase 3 — Filters and drill-down

- `FilterBar` with shared local filters across components bound to the same
  evidence.
- `@Drill` action step, validation in `action_context`, structured intent into
  the follow-up turn, and audit for accepted and refused drills.

**Acceptance:**

- On the field-sales set, selecting an outlet on a map, a bar or a grid row and
  choosing "break down by month" starts a new turn that runs a new query under
  the caller's active role; a non-root user with a Ranger row filter sees only
  permitted rows in the drill result.
- A drill on a field not in the evidence or ontology is refused and audited.
- Tampered client state or rows do not change the drill (existing
  `program_sha256` and server-side selection checks hold).
- "Get all rows" on a capped grid runs a governed follow-up; it does not
  stream extra rows into the old block.

### Phase 4 — Ontology vocabulary

- At compose time, resolve evidence columns to metrics and concepts in the
  released ontology the turn is pinned to, and store the refs in provenance.
- `Kpi`, `Chart`, `Comparison` and `MetricDefinition` use the ontology name,
  unit, direction and definition when present, column labels otherwise.

**Acceptance:**

- A KPI over a metric with an ontology binding shows its released name and
  definition; after a new release, replay of an old turn still shows the
  release it was pinned to.
- No ontology lookup broadens what the caller can see: unreleased or
  unauthorized definitions are not shown.

### Phase 5 — One chart look

- Render UI chart nodes through `VizView` (the Phase 0 decision) so tool charts and UI chart nodes share one
  renderer, legend, palette, highlight and insight headline.
- Remove the duplicated chart drawing code only; `IntelligentUi`, the catalog
  and the validator stay.
- Update [Arch 16](../arch-16-viz-core.md) and the intelligent UI architecture
  page to describe the result.

**Acceptance:**

- One answer containing a tool chart and a UI chart shows them with the same
  axes, palette and interaction (screenshot tests, light and dark).
- Saving a chart to BI behaves as before.

---

## 8. Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Larger prompt | More tokens per turn, slower first token | Data section first, general section shorter; measure in the agent eval scorecard and benchmarks. The deterministic composer covers turns where the model skips UI. |
| Visual noise | Every data answer grows a widget, including ones a sentence answers well | One primary data component by default; Kpi for single values; the decision table is tested against "answer is one number" cases. |
| Partial results read as totals | Wrong business decisions | Partial notice is rendered by the component from provenance, not written by the model. Totals over omitted rows are labelled. |
| Drill-down as an access path | Users reach data through the UI they could not query | Drill is a normal turn under the caller's principal and role, through Ranger; fields are validated; refusals audited. |
| Validator latency and capacity | Four Node slots, 5 s timeout per program | Deterministic programs are small; track `studio_operation("ui", "validate")` latency before and after Phase 1. |
| Replay breakage | Old conversations stop rendering | Catalog versions stay supported; screenshot tests cover v2 and v3 blocks. |
| Ontology drift | A KPI label changes meaning between releases | Store the release ref in provenance and render the pinned definition. |
| Two chart renderers during Phases 1–4 | Inconsistent look until Phase 5 | Accepted for the interim; Phase 5 is scheduled, not optional. |

## 9. Open questions for the product owner

1. **Chart renderer.** Resolved on 6 Nov 2026: `VizView`, fed by a
   browser-side records-to-`PresModel` adapter (see "Phase 0 results").
2. **Default component for wide results.** When nothing in the decision table
   fits, is a data grid the right default, or should the answer stay prose with
   the folded "Source data"?
3. **Drill scope.** Should `@Drill` be limited to the dimensions in the bound
   evidence for the first release, or include ontology-linked dimensions from
   the start?
4. **General components in data turns.** Should the validator refuse general
   components (`Flashcard`, `YouTube`, `Celebration`) in a turn whose evidence
   is business data, or only demote them in the prompt?
