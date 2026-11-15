# NOVA NATIVE DATAVIZ — DESIGN SYSTEM & UI IMPLEMENTATION CONTRACT
**Version 1.0 · Source of truth:** current repository `DESIGN.md` and `frontend/src/styles/theme.css`. The two supplied/generated mockup PNGs describe **layout and behavior**; they are NOT a competing color palette or font system.

## A. Design intent and product language

**Mental model:** One data story, two work modes. **Dataviz** is the full-width *manual* analytics studio. **Agent** is a collaborative workspace where Neva acts on the *same document* through native tools. “Canvas” and “Data” are content tabs within one dashboard editor.

**Visual goals:** editorial hierarchy; data-first legibility; calm whitespace; responsive efficient density; semantic controls; visible state; predictable keyboard/touch behavior; Neva presence without obscuring real work. Grid squares are an editing affordance, not a permanent dashboard background. Designs are not production output until responsive, accessibility, interaction and security gates pass.

**Design principles:**

1. A *single* UI design language from Nova tokens. Do not copy generic blue/purple buttons from the reference concepts into production.
2. Dataset/field/metric/source trust always visible when relevant; no ungrounded KPI values or fake previews.
3. Every visible action has a tested behavior; every edit has an exact saved/draft/failed status.
4. Read mode is visually clean; Edit mode exposes structure (grid, handles, inspector, drop targets).
5. Agent presence is clearly labeled “Neva” and never impersonates the human cursor.
6. Rendered data is never authoritative for the resource's permission or saved definition.
7. All screens pass loading / empty / unauthorized / error / success / offline / conflict / stale-source states.

## B. Existing Nova tokens — mandatory reuse

All color definitions live in `frontend/src/styles/theme.css`; do not write CSS hex, HSL, RGB, Tailwind palette names or color literals in feature code. Use semantic classes and existing `readToken` bridge when ECharts needs concrete canvas color values.

| UI role | Nova token(s) | Notes |
|---|---|---|
| App outer canvas | `--background`, `--foreground` | Data and Canvas work surfaces |
| Inspector/cards | `--card`, `--card-foreground`, `--surface-1/2/3` | Nested panel backgrounds; no arbitrary opacity tricks |
| Grid and boundaries | `--border`, `--surface-border`, `--viz-grid`, `--viz-axis` | Subtle edit dots/lines |
| Selection/focus | `--ring`, `--primary`, `--primary-foreground` | Strong visible focus outline and active edit indication |
| Actions | `--primary`, `--secondary`, `--accent` | Primary is current Nova reddish brand, not mockup blue |
| Data series | `--chart-1` .. `--chart-9`, `--chart-tone-1` .. `--chart-tone-6` | Only for encoding values; never use as arbitrary button colors |
| Chart marks | `--viz-mark`, `--viz-line`, `--viz-reference`, `--viz-highlight` | Reserved semantic chart roles, obey existing rules |
| Status | `--success`, `--warning`, `--info`, `--destructive` and `*-strong` | Correct contrast/surface checks |
| Muted UI | `--muted`, `--muted-foreground`, `--ink-secondary` | Secondary descriptions and metadata |
| Pills | `--pill-discrete*`, `--pill-continuous*` | Discrete dimension vs continuous measure semantics |
| Typography | `type-display`, `type-title`, `type-figure`, `type-ui`, `type-label`, `type-meta`, `type-code`, `viz-numerals` | Already defined in `theme.css`; no bespoke 10px UI |

**New semantic cases** such as AI presence halo or drag ghost must first be implemented as *semantic aliases based on current tokens* inside `theme.css` (and added to `DESIGN.md` + contrast tests), never by adding per-component new hex values. Visual reference blue outline maps to Nova selection/focus semantics rather than a new brand color.

**Fonts:** Inter is the current UI family, system/JetBrains mono for SQL/field IDs, as documented in `DESIGN.md`. Standard body 14px, metadata 12px minimum; no new smaller labels. Use Nova's existing theme-weight utilities.

## C. Sizing, spacing and responsive composition

These are initial design targets, adjustable only after measured usability testing. Use 4px spacing rhythm and Nova utility tokens; no repeated magic offsets.

| Region | Desktop >=1440 | Medium 1024–1439 | Narrow <1024 |
|---|---|---|---|
| Left application navigation | Existing Nova nav width; no duplicate custom nav | Existing collapsible nav | Icon rail / drawer |
| Header | Product + dashboard title + status + sharing actions | Wrapping overflow menu for secondary actions | Compact title + overflow |
| Canvas | Remaining width, canonical grid 12 cols | Visual reflow 6-cols | Viewer 1-col; editing in dedicated mode/drawer |
| Right inspector | 336–376px fixed track when selected | 320px collapsible inspector | Modal/bottom sheet with keyboard focus management |
| Add-widget palette | 56–64px docked/floating toolbar | Floating toolbar | Accessible Add menu |
| Dataviz editor header | 56–64px | 56–64px | 48–56px |
| Data/Canvas tabs | 40–44px row | 40–44px row | Scrollable tabs with hit targets |
| Grid cellHeight | ~72px canonical | 72px equivalent with reflow | Derived stack, not persisted as authoritative |
| Gap between cards | 8px initial | 8px | 8px |
| Dashboard widget | min 2×2 canonical; KPI commonly 3×2; chart commonly 6×4 | Min readable width >=240px | Full-width, content-driven height |
| Agent chat | ~360–420px recommended, min 320px | Collapsible/resizable | Tab/sheet view; never horizontal squeeze |
| Artifact Panel | Remaining width, practical min 520px for full editor | Inspector collapses first | Full-screen editor with return-to-chat |

### C.1 Dataviz desktop wireframe

```text
+----------------------------------------------------------------------------------+
| Nova | Dataviz | Search...                  Draft v3  Publish  Share  [Ask Neva] |
+------------------+---------------------------------------------------------------+
| Nav              | Sales Dashboard       Canvas | Data          Page 1 [+]      |
|                  +----------+---------------------------------------+------------+
|                  | Widget   | 12-column EDIT GRID                   | Inspector  |
|                  | palette  | [ KPI ][ KPI ][ KPI ]                 | Configure  |
|                  |          | [   Bar Chart   ][ Line Chart ]      | Style      |
|                  |          | [        Table / Pivot         ]      | Interact   |
|                  |          |                                       | Filter     |
+------------------+----------+---------------------------------------+------------+
```

**Data sub-tab** replaces canvas content with `Datasets` navigator + SQL/visual model center + Fields/Params/Lineage right. Tabs retain unsaved draft and local selection across switching. No new Studio top-level tab.

### C.2 Agent desktop wireframe

```text
+----------------------------------------------------------------------------------+
| Nova Studio                        Agent | Dataviz                                |
+------------------------+---------------------------------------------------------+
| Neva chat              | Artifact Panel | Spreadsheet Whiteboard HTML Chart Dash |
| progress + approvals   | [ Dashboard: Sales ]  [Saved] [History] [Export]       |
| user corrections       | [Page 1] Canvas | Data  [inspector toggle]             |
|                        | [ KPI ][ KPI ] [selected chart + Neva cursor]          |
| [Message Neva...]      | [       chart / filters / table       ]               |
+------------------------+---------------------------------------------------------+
```

The same `NativeDashboardCanvas`, `ChartEditor`, `DatasetEditor`, `FilterBuilder`, and `DocumentToolbar` components mount in both contexts. Only shell density and inspector presentation differ. Do not fork business logic or create a “simple Agent canvas” with fewer data semantics.

## D. Component anatomy & behavior

### D.1 `DashboardHeader`
- Title editable by editor; description/owner/last saved; status `Draft`, `Published`, `Saving…`, `Conflict`, `Blocked`, `Stale source`.
- Buttons: Save (manual immediate commit), Publish (requires permission+review), Share, Version History, Export, More.
- View mode shows current publication and data freshness; draft editor has explicit revision.
- `Ask Neva` opens existing assistant context; never creates a second agent runtime.

### D.2 `CanvasToolbar`
- Mode: View/Edit; Undo/Redo; 12-column grid settings; Show grid toggle (Edit only); Snap-to-grid (Edit only); Zoom; Fullscreen.
- Keyboard: Escape cancels drag, Arrow keys nudge selected widget in Edit (with modifiers for resize), Undo is scoped to document operations and does not erase unrelated collaborator edits.
- Disabled controls show contextual why-not; never inert.

### D.3 `WidgetFrame`
- Header: chart title, optional icon + menu, aria label; hover handle appears only to editors; resize affordance bottom right.
- Body: ECharts/KPI/TanStack with robust zero/no data/error/loading states, visible applied filter chips only where needed.
- Target selection: outline via Nova focus/selection token, handles and targeted Inspector. Selected mark highlight is different from widget edit selection.
- Interactions: chart click->crossfilter, context menu->drill, click blank->clear select; keyboard equivalent; `aria-live` for query status.

### D.4 `ChartInspector`
- Header selected chart name and close, tabs `Configure` / `Style` / `Interactions`.
- Configure sections in order: Dataset, Visualization Type, X Axis, Y Axis, Color/Group By, Labels, Formatting, Filters; relevant series-specific configuration toggles, drag sortable fields only if keyboard alternative exists.
- Style: title/description, axes/legend/tooltip, number format, reference line/threshold, theme palette, data label density, sort/topN, accessible palette.
- Interactions: select target action, target widget list/scope, drill path, tooltip settings.
- Right filter builder appears as nested panel, modal or side overlay; heading/context preserved on return.

### D.5 `FilterBuilder`
- Choose dataset/source field from authorized field picker (physical+semantic label, type); choose filter type; choose scope and mapping; configure title/description, default/all/null/search options, values/relative period; preview impacted widget count.
- Controls: Single value, Multiple values, Date picker, Date range, Text entry, Number range slider; optional Relative date and Boolean filter.
- Field picker shows type icon and fully qualified semantic source, searchable/virtualized, no masked values in previews.
- Filter chip has clear state, default vs overridden indicator, accessibility label and error state. `Allow All` explicitly means no predicate for this filter (not all rows accessible independent of Ranger).

### D.6 `DatasetEditor`
- Left dataset list; action `Add Data` (table/view/semantic) or `Add SQL`.
- Center Monaco SQL with bounded Preview table and schema/field list. Query text marked read-only validated, parameter editor and refresh result. Changed SQL does not automatically invalidate published dashboard definition.
- Right tabs Fields, Parameters, Lineage, Quality. Visible source grant/role context and diagnostics. No unbounded `SELECT *` preview against giant tables.

### D.7 `ShareDialog`
- Users / Roles tabs with explicit permission checkboxes or presets `Read-only` (view), `Viewer` (view + interact), `Commenter`, `Editor`, `Publisher`, `Manager`, plus separate `Export`.
- Explain: dashboard share does NOT grant access to database data. Show last actor/grant date and revoke action. Successful save uses commit receipt, updates open sessions.

### D.8 `NevaCursorOverlay`
- Distinct virtual pointer with `Neva` label, placed relative to scrollable canvas coordinate space; tool operation target by `widget_id` or inspector control name.
- Smooth deterministic Bézier with distance-dependent speed; small controlled corrections; reduced motion = simple highlight.
- When Neva does an actual edit, show pending -> confirmed progress; if conflict, cursor stops with a review state, not a fake completion.
- Human pointer focus wins; do not move user's operating system cursor or dispatch synthetic click events into editor.

## E. Interactive state specification

| Trigger | UI reaction | Network/data effect |
|---|---|---|
| Select chart in Edit | Handles/Inspector open | None (already loaded definition) |
| Drag chart to a new cell | Ghost placeholder/snapped preview | Only commit once on release with CAS |
| Resize chart | Bounds redraw, ECharts `resize()` | No query unless data config changed |
| Change X/Y binding | Preview with spinner and valid schema | Cancel prior query, compile authorized plan |
| Add filter | Highlight scoped targets and show derived values | Preview authorized option query, commit definition on Apply |
| Change filter in View | Active chip + skeleton on targets | Requery only matching widgets; current viewer state only |
| Cross-filter mark click | Dim nonselected marks, chips, data updates | Typed filter and bounded target requery |
| Publish | Confirm affected charts/datasets and change summary | PG immutable publication and audit |
| Share role/user | Receipt state, live viewers reauthorized | ACL transactional update + invalidate caches |
| Neva drags widget | Labeled cursor + diff preview | Same `dashboard.operations` API as manual UI |
| Neva errors / blocked | Agent status explanation; no green cursor | No commit unless durable receipt exists |
| New chat + @artifact | Reopen latest authorized definition | Resolve stable artifact ID + auth/role |
| Permission revoked | Close panel/WS if needed, protected data removed | 403/404 and cache purge |
| Source schema changed | `Source changed` diagnostic and safe repair | No wrong field binding or silent fallback |
| Offline | No false saved indicator | Pending edits remain local until explicitly resolved |

## F. Accessibility & interaction QA

- WCAG 2.2 AA target for controls, focus, contrast, content labeling. On dark and light themes, use Nova token-based contrast checks.
- All chart operations possible with keyboard through inspector; drag-only UI is insufficient.
- Tables expose actual cell roles and textual alternatives. Chart tooltips use accessible data table/summary.
- Minimum 44px effective touch targets where practical; avoid tiny 10px icons for essential features; smallest text 12px per Nova.
- Cursor/drag animation complies with reduced motion, pointer capture and cancellation; must not steal text editing focus.
- Test long field names, multilingual labels, RTL where supported, 100+ option virtual list, 0/1/negative/NaN-like data, chart error and partial dataset permission.
- Screenshot baselines: 1920×1080, 1440×900, 1280×720, 1024×768, 768×1024, 390×844, both light/dark, Edit/View/Agent cursor/selected inspector/filter builder/Data SQL/published/shared/error.

## G. CSS implementation guide (no competing color palette)

```css
/* Illustrative only. Add under existing Nova feature stylesheet structure. */
.nova-dataviz-shell {
  background: var(--background);
  color: var(--foreground);
}
.nova-dataviz-inspector,
.nova-dataviz-widget {
  background: var(--card);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius);
}
.nova-dataviz-canvas[data-mode="edit"] {
  background: var(--background);
  /* Grid pattern may use --viz-grid through a theme-controlled alias. */
}
.nova-dataviz-widget[data-selected="true"] {
  outline: 2px solid var(--ring);
  outline-offset: 2px;
}
.nova-dataviz-widget:focus-visible {
  outline: 2px solid var(--ring);
}
```

GridStack CSS classes must be scoped under Dataviz canvas. Layer z-index intentionally: dashboard canvas < widgets < drag placeholder < inspector popover < dialog; Neva cursor overlay is pointer-events:none and must not intercept controls. ECharts color receives dynamic values via `readToken` and refreshes on theme switch.

## H. No implementation ambiguity

- Canvas pixel placement/rendering never defines document state; 12-column `x,y,w,h` does.
- Grid is visible in Edit and hidden in View, unless user explicitly chooses to inspect layout.
- Filter chips always represent actual active state; no fake numbers or illustrative data outside empty-state placeholder explicitly labeled as such.
- Agent and Dataviz share **components, APIs, metadata and revision IDs**. Changes committed in one appear in the other.
- Visual mockup assets are not authoritative for component colors/typefaces, user names, chart numbers, or invented performance badges.
- An edit is considered complete only when the **server** confirms the committed revision; “Neva is moving” is not “saved”.

## I. Reference assets in this package

- `REFERENCE_DATAVIZ_BUILDER.png`: full-width designer layout inspiration.
- `REFERENCE_AGENT_COLLAB.png`: Chat + Artifact Panel collaboration layout inspiration.

These images are illustrative concepts, not exact screenshot regression goldens. Build test fixtures with deterministic real values and update approved screenshot baselines from the implemented Nova UI.
