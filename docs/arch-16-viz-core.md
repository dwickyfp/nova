# Arch 16: The Viz Core (worksheets, calculations, dashboards)

Status: current. The viz core is the active engine behind Studio charts and
dashboards.

## What it is

Studio artifacts have exactly two kinds: **chart** and **dashboard**. Both are
backed by the viz core in `backend/app/modules/viz/`. A chart is a worksheet
(fields on shelves, a marks card, filters and calculated fields) compiled to
one StarRocks query; a dashboard lays chart artifacts out in zones. StarRocks
is the only execution engine, the shared assistant loop is the only agent
loop, and every query runs through `QueryService` under the caller's session
and active role.

The design follows the shelf-and-marks model of visual analysis described in
the public literature (the Polaris / VizQL papers) and the behavior documented
for analyst tools of that family: discrete vs. continuous fields, level-of-
detail expressions, and table calculations. It is implemented from those public
semantics only; no third-party product code or assets are used.

| Layer | Module | Responsibility |
| --- | --- | --- |
| Document model | `viz/spec.py` | `WorksheetSpec`: column/row shelves of field pills, marks card (color/size/label/detail/angle), filters, sort, top-N, quick table calculations, calculated fields. `DashboardSpec`: a 24-column tiled zone layout over chart artifacts with quick filters and filter/highlight actions |
| Calculation language | `viz/calc/` | Formulas: tokenizer and parser (`syntax.py`), function catalog (`functions.py`), type and level checker (`analyzer.py`), SQL lowering (`lower.py`) |
| Query compiler | `viz/compiler.py` | The worksheet as one layered StarRocks `SELECT` |
| Chart catalog | `viz/showme.py` | Show Me: chart-type requirements and ranking, and the field-placement engine that turns a chart intent into a worksheet |
| Presentation model | `viz/presmodel.py` | Classified columns, axes, legend and totals for the renderer; computes in memory for chat results and passes engine-computed rows through |
| Service | `viz/service.py` | Probe, compile, execute, data pane, formula checks, filter domains, persistence, dashboard composition |

## The calculation language

Calculated fields (`WorksheetSpec.calculations`: name, formula, optional
`addressing`) are written in a small expression language:

- fields as `[Order Date]` (or a bare `sales`), literals `12.5`, `'East'`,
  `#2024-01-31#`, `TRUE`, `NULL`; `//` and `/* */` comments;
- operators `+ - * / % ^`, comparisons, `AND`/`OR`/`NOT`, `IN (...)`;
  `+` concatenates text and adds days to dates, `date - date` is days;
- `IF ... THEN ... ELSEIF ... ELSE ... END`, `CASE ... WHEN ... END`, `IIF`;
- row functions (number, string, date, type conversion), aggregates (`SUM`,
  `AVG`, `MEDIAN`, `COUNT`, `COUNTD`, `MIN`, `MAX`, `STDEV`, `VAR`,
  `PERCENTILE`, `ATTR`, ...), level-of-detail expressions
  `{FIXED|INCLUDE|EXCLUDE dims : aggregate}` and `{aggregate}`, and table
  calculations (`RUNNING_*`, `WINDOW_*`, `LOOKUP`, `TOTAL`, `INDEX`, `FIRST`,
  `LAST`, `SIZE`, `RANK*`).

`GET /studio/viz/calc/functions` serves the catalog with syntax and
descriptions. Every function lowers to a StarRocks built-in verified on the
pinned engine (for example `MEDIAN` → `PERCENTILE_CONT(CAST(x AS DOUBLE),
0.5)`; `DATEDIFF` counts part boundaries by comparing truncated dates).

The analyzer gives every expression a data type and an evaluation level —
`row`, `aggregate` or `table` — and enforces the rules that make a worksheet
compile to one well-formed query: row and aggregate values do not mix, an
aggregate cannot aggregate an aggregate (use an LOD), table calculations take
aggregated arguments, LOD bodies are aggregates, and INCLUDE/EXCLUDE cannot sit
inside FIXED. Errors carry the exact source span, which the formula editor
marks. Calculated fields may reference each other; cycles are rejected and
references are inlined before planning.

The language is a worksheet formula language compiled to SQL, not SQL syntax:
it lives in the viz core rather than the SQL frontend, and its output is always
a read-only `SELECT` over the worksheet's source that still passes through
`QueryService`.

## Order of operations

`compile_worksheet` builds the view as layers (CTEs), in this order:

1. the source (one read-only statement or a table) as a subquery;
2. context filters (`filter.context = true`);
3. FIXED and table-scoped LOD values, each computed by its own grouped
   query and joined back to rows with null-safe equality (`<=>`);
4. dimension filters on row-level fields (including FIXED results);
5. INCLUDE/EXCLUDE LOD values at the view's dimensions plus/minus the listed
   ones (EXCLUDE of a date also drops its date-level pills);
6. aggregation to the view's dimensions; every aggregate term is hoisted to a
   named column;
7. measure filters (`QuantitativeFilter.aggregation`, or a filter on an
   aggregate calculation) on the hoisted aggregates;
8. table calculations as window functions, layered when nested, partitioned by
   the view dimensions not in the calculation's `addressing` and ordered along
   the addressed ones (the whole table when `addressing` is null);
9. the final projection, sort and limit. A view with table calculations is
   ordered by its dimensions, and the presentation model keeps that order.

An aggregate whose argument is built only from LOD values (`SUM({FIXED
[Region] : SUM([Sales])})`) aggregates each LOD group once — values coarser than
the view repeat per mark, finer values roll up — instead of once per source
row. A plain view (no LODs, measure filters or table calculations) compiles to
a single `SELECT ... GROUP BY` so the SQL tab stays readable.

Date dimension pills carry `date_part` (year/quarter/month/week/day): discrete
pills lower to the part (`MONTH(d)`), continuous pills to a truncation
(`DATE_TRUNC('month', d)`). Pill aggregations include sum, avg, median, count,
count distinct, min, max, stdev(p), var(p) and attr; `aggregation = "agg"` marks
a pill over an aggregate or table calculation.

## Execution paths

1. **Chat** (`data_to_chart` tool): the turn's verified table is classified,
   the model returns a small typed chart intent (or Show Me picks
   deterministically), and the presentation model is computed in-core from rows
   already redacted and held. The chart SSE frame carries `chart_spec`
   (Vega-Lite mirror for replay), `viz_spec`, and `presmodel`. Saving attaches
   the producing statement as the worksheet's source.
2. **Compute** (`POST /studio/viz/compute`): probe the source (100 rows, under
   the caller's access) for columns and inferred types, compile, execute, and
   return the presentation model, the SQL, the data pane (`fields`) and the
   resolved worksheet.
3. **Refresh** (`POST /studio/artifacts/{id}/refresh`): a chart re-compiles its
   worksheet against current data under current RBAC. A chart saved before
   worksheets existed (Vega-only, or a plain query) re-runs its stored SQL and
   gets a worksheet derived from that SQL and its saved encoding
   (`viz_service.derive_worksheet`, returned as `derived_viz_spec` with a
   presentation model); nothing is stored until the editor saves it. Dashboard
   zones over such charts use the same derivation.
4. **Dashboard** (`POST /studio/viz/dashboard-compute`): every sheet zone
   re-compiles with filter-zone values and filter-action selections merged
   (`WorksheetSpec.with_extra_filters`); failures are per zone.
5. **Authoring**: `POST /studio/viz/describe` (data pane and a sample),
   `POST /studio/viz/calc/check` (type, level and error span for one formula),
   `POST /studio/viz/field-domain` (members, or min/max for numbers, of one
   field under the worksheet's context filters), and
   `PATCH /studio/artifacts/{id}/spec` to save. A chart's SQL provenance never
   changes from the editor.

## Security invariants

- Every execution goes through `query_service.execute_statements` with the
  caller's credentials and active role; the viz core holds no connection.
- Formulas never reach SQL as text: they are parsed, checked and lowered;
  identifiers are backtick-quoted and validated against source columns when
  known; string literals escape backslashes and quotes (StarRocks treats `\`
  as an escape); numbers are validated; date parts, offsets and rank
  directions must be constants from fixed sets.
- Source columns and calculation names cannot start with the reserved
  `__nova_` prefix the compiler uses for its own columns; names starting with
  `__` are reserved in formulas.
- Dashboard zones may only reference chart artifacts owned by the same user; a
  shared dashboard reads the owner's definitions but executes as the viewer.
- Chart artifacts store one read-only source statement (`validate_artifact_sql`);
  the worksheet stores no rows.

## Migrations (`viz/migration.py`, boot via `app/main.py`)

1. `viz_spec` JSON column added to `CONFIG_STUDIO_ARTIFACTS`.
2. Legacy `table` artifacts become `chart` (no viz spec: they render as grids).
3. Legacy `CONFIG_STUDIO_DASHBOARDS` rows become dashboard artifacts with
   deterministic ids (idempotent); the legacy table is never written again.
   Smart Reports keep their own history; the boot-time
   dashboards-into-reports migration is retired.

New worksheet fields (`calculations`, `date_part`, filter `context` and
`aggregation`) are optional, so saved worksheets keep validating unchanged.

## Frontend

- `features/studio/artifact-explorer.tsx` is the content browser: All /
  Dashboards / Sheets / Favorites tabs, search, sort (modified, created, name,
  type), grid of live thumbnails or a sortable list, favorites and layout as
  per-browser conveniences, item actions (open, edit, refresh, delete), and
  New → sheet from a query or empty dashboard. Dashboard thumbnails draw each
  zone with its sheet's marks.
- Dashboard sheet zones offer "Edit sheet", which opens that chart in the
  worksheet editor.
- `features/viz/worksheet-editor.tsx` is the authoring surface: a data pane
  (dimensions, measures, calculated fields, record count), Columns/Rows
  shelves, Filters and Marks cards, Show Me, pill menus (aggregation,
  dimension/measure, discrete/continuous, date levels, sort, quick table
  calculation, filter, edit calculation), undo/redo, swap and clear, and the
  generated SQL. Drag and drop works between the pane, shelves, cards and back
  (to remove); every drag has a menu equivalent for keyboard users.
- `features/viz/worksheet-model.ts` holds the pure pill/shelf operations and
  the history reducer.
- `features/viz/calc-editor.tsx` is the formula editor (Monaco, `nova-calc`
  language with field/function completion, live server check with error
  markers, function reference, and "compute using" for table calculations).
- `features/viz/filter-dialog.tsx` edits member, range, relative-date and
  measure filters, with "add to context".
- `features/viz/insight.ts` computes a view's answer from its own rows
  (leader, share, gap, average, trend, low point) and the one mark it is
  about; Explore, the sheet preview, the editor and dashboard zones show it as
  the headline and draw that mark in `--viz-highlight` (DESIGN.md 2.6).
- `features/viz/viz-view.tsx` renders a presentation model on Nova tokens; pills
  use `--pill-discrete`/`--pill-continuous`. Several dimensions on an axis read
  as combined headers; several measures on an axis mark get one pane each with
  their own axis; a thumbnail mode draws marks only.
- `features/viz/dashboard-canvas.tsx` is the zone canvas with quick filters and
  cross-filter mark clicks.

## Out of scope

Image-tile rendering, a desktop host bridge, a second execution engine, native
connectors, and cloud-hosted AI dependencies. StarRocks and Nova's assistant
loop own those responsibilities.
