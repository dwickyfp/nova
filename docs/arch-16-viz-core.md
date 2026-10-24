# Arch 16: The Viz Core (Tableau-adapted intelligence dashboards)

Status: current. The viz core is the active engine behind Studio charts and
dashboards.

## What it is

Studio artifacts have exactly two kinds: **chart** and **dashboard**. Both are
backed by the viz core in `backend/app/modules/viz/`, an adaptation of
Tableau's visualization architecture to Nova: StarRocks replaces Hyper as the
execution engine, the shared assistant loop replaces the Qt host, and the SQL
frontend's guard/RBAC path is the only execution path.

| Tableau concept | Nova adaptation |
| --- | --- |
| `VisualSpecification` (shelves, marks card) | `WorksheetSpec` (`viz/spec.py`): column/row shelves of field pills, mark type, color/size/text/detail/angle encodings, filters, sort, top-N, table calculations |
| `Zone` / `ZoneLayoutType` dashboard layout | `DashboardSpec`: a 24-column tiled zone tree (`sheet`/`text`/`filter`/`spacer`) over chart artifacts, plus filter/highlight actions |
| `vizdataservice` semantic query | The compiler (`viz/compiler.py`): aggregation, quantitative/categorical/relative-date filters, window-function table calculations, compiled to one StarRocks SELECT with the source wrapped as a CTE |
| `ComputeView` presentation model | `viz/presmodel.py`: classification, aggregation, calcs and totals computed in-core, idempotent over SQL-pushed-down rows |
| ShowMe (`ShowMeTypeResolver`, `ChooseForMePicker`) | `viz/showme.py`: chart-type requirements and ranking ported from Tableau's catalog, plus the field-placement engine |
| Tableau Agent `NotionalSpec` | `NotionalSpec`: the AI↔viz bridge. `data_to_chart` asks the model for a small typed notional, the ShowMe engine resolves the worksheet deterministically |
| Hyper engine | StarRocks through `QueryService`, always under the caller's session and active role |

## Execution paths

1. **Chat** (`data_to_chart` tool): the turn's verified table is classified,
   the model returns a notional spec (or ShowMe picks deterministically), the
   presentation model is computed in-core from rows already redacted and held.
   The chart SSE frame carries `chart_spec` (Vega-Lite mirror for replay),
   `viz_spec`, and `presmodel`. Saving attaches the producing statement
   (exposed on `context.last_result.sql`) as the worksheet's source.
2. **Refresh** (`POST /studio/artifacts/{id}/refresh`): a viz chart re-compiles
   its worksheet (`probe → compile → execute → presmodel`), so refresh honors
   the saved shelves and filters against current data under current RBAC.
   Legacy Vega-only charts re-run their stored SQL unchanged.
3. **Dashboard** (`POST /studio/viz/dashboard-compute`): every sheet zone
   re-compiles with filter-zone values and filter-action selections merged
   (`WorksheetSpec.with_extra_filters`); failures are per zone.
4. **Authoring** (`PATCH /studio/artifacts/{id}/spec`): the Studio editor
   saves an edited worksheet or zone layout; a chart's SQL provenance never
   changes from the editor.

## Security invariants

- Every execution goes through `query_service.execute_statements` with the
  caller's credentials and active role; the viz core holds no connection.
- Field names are validated against source columns when known and always
  backtick-quoted; filter literals are escaped or strictly validated
  (`compiler.quote_ident` / `quote_literal`).
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

## Frontend

- `features/viz/viz-view.tsx` renders a presentation model (Tableau marks on
  Nova tokens; pills use `--pill-discrete`/`--pill-continuous`).
- `features/viz/viz-editor.tsx` is the authoring surface: data pane,
  Columns/Rows shelves, marks card, ShowMe strip.
- `features/viz/dashboard-canvas.tsx` is the zone canvas with quick filters and
  cross-filter mark clicks.
- The old tile-dashboards UI (`studio-dashboards.tsx`) and its API are removed;
  `view=dashboards` routes to the artifacts library.

## Not ported (by design)

Hyper/LLVM, Qt/QWebChannel hosting, VizQL image tiles, the Java JDBC bridge,
native connectors, Tableau's cloud (Einstein/Waii) dependencies. StarRocks and
Nova's assistant loop own those responsibilities.
