# ADR-001: Free-form dashboard canvas (dashboard schema 3)

- Status: accepted. P0 (contract and upcaster), P1 (frame renderer) and P2
  parts 1 (selection, snapping, arrange commands) and 2 (shapes, icons, lines,
  formatting, layers and tab order) are implemented; P2 part 3 (images) and P3
  are open.
- Owner decisions: one free-form canvas engine replaces GridStack, with no
  hybrid mode or feature flag. Grid assistance is an authoring aid only. The
  Dataviz assistant becomes Neva with per-batch approval (P3).
- Scope: native Dataviz dashboards. Dataset and chart documents stay schema 2.

## Context

Schema-2 dashboards were 12-column GridStack grids: every widget had an integer
cell `{x, y, w, h}` with 72 px rows and a 6 px margin. A grid cannot express free
positions, overlap, stacking order, shapes, connectors, annotations, groups, a
fixed page size or a reading order separate from the visual order. Immutable
revisions and operation receipts already exist for these documents, and
integrations (including the Dataviz assistant tools and Studio publish) speak
grid cells.

## Decision

### Document contract

`DashboardDefinition` is schema 3 (`backend/app/modules/viz/contracts/dashboards.py`);
the verbatim schema-2 models live in `contracts/dashboards_v2.py`. Generated
TypeScript types, JSON Schema and Zod validators come from
`backend/scripts/generate_viz_contracts.py`; its drift check covers both.

- A page has a `canvas`: a size preset (`16:9`, `4:3`, `a4-landscape`,
  `letter-landscape`) or `custom` width and height, a fit mode
  (`page`, `width` for long scrolling pages, `actual`), a background and a
  `grid`. The grid is authoring help only: `columns` (default 12), `gutter`,
  `margin`, `baseline` (8 pt rows), `show`, `snap` and `snap_to_elements`.
- Elements are a discriminated union by `kind`: `visual`, `filter`, `text`,
  `image`, `shape`, `icon`, `connector`, `annotation` and `group`. Every element
  has an id, a name, integer pixel `geometry` with a quarter-turn `rotation`,
  `locked`, `hidden`, `decorative` and a token-based `style` (fill, stroke,
  radius, shadow, opacity, padding). Array order is stacking order.
- Text is rich text: plain runs and `ValueBinding` runs that read one governed
  value from a visual's own query result. `DatumAnchor` attaches annotations and
  connector ends to a data point by match, rule or measure; a missing datum hides
  the attachment instead of guessing.
- `tab_order` gives an explicit keyboard order; otherwise reading order is top
  to bottom, then left to right, with a 24 px row tolerance (`canvas.reading_order`).
  `mobile.order` and `mobile.hidden` project the page for phones.
- Limits: at most 150 elements per page (groups included), group depth 3,
  elements may overhang the page by 64 px, and a document is at most 1 MiB.
  Cross references (connectors, anchors, bindings, filter targets, interaction
  sources, tab and mobile lists) are validated.

### Upcaster

`backend/app/modules/viz/canvas.py` upcasts schema 2 on every read,
deterministically and losslessly. Stored revisions are never rewritten.

- A schema-2 page becomes a `custom` page 1,200 px wide with `fit: width`, height
  `max(720, bottom row × 72)`, and the grid `{columns 12, gutter 12, margin 6}`.
  At that width a column is exactly 100 px, so a cell maps to integer pixels with
  no rounding: `x = col·100+6`, `w = cols·100−12`, `y = row·72+6`, `h = rows·72−12`.
- Widget appearance maps to element style (card → `card` fill, border →
  `surface-border` stroke, padding none/compact/comfortable → 0/12/16, radius 8);
  text becomes one plain run with role `title` or `body`.
- Spacers are not elements; they are kept as `page.legacy_spacers`, never drawn,
  so the round trip stays exact.
- `legacy_widgets(page)` is the exact inverse projection used by grid-speaking
  clients. The browser mirror (`frontend/src/features/native-dataviz/dashboard/layout.ts`)
  is tested against the same golden fixtures as the server.

### Operations and receipts

Schema-3 operations are typed changes on the existing `Operation` with revision
CAS: `elements.add`, `elements.update` (a patch; locked geometry is refused unless
the same patch unlocks), `elements.remove` (dependent connectors, annotations and
emptied groups go with it), `elements.arrange`, `elements.reorder`,
`page.canvas.set`, `page.tab_order.set` and `page.mobile.set`.

The schema-2 changes keep their exact wire shape and are adapted on the server:
`layout.apply` becomes `elements.arrange` (an unchanged cell keeps its exact
geometry; a partial placement is allowed), `widget.add`/`widget.replace`/
`widget.remove` become element changes (replace keeps canvas-only properties, the
style when the appearance is unchanged, and rich text when the plain text is
unchanged), and `page.add` accepts a schema-2 page. Because request digests are
computed over the submitted shape, old receipts still replay and a retried legacy
operation returns its original receipt. Create and replace digest the submitted
(possibly schema-2) definition and store the upcast form.

### Storage

Migration `0063_viz_dashboard_canvas.sql` allows `viz_revisions.schema_version`
2 or 3 and requires it to equal the stored definition's `schema_version`
(absent means 2). New dashboard revisions are schema 3; existing rows keep their
definition and digest.

### Renderer (P1)

`frontend/src/features/native-dataviz/dashboard/canvas.tsx` draws a page as a
frame; the pure geometry lives in `frame.ts`. GridStack is removed.

- The page has a logical size (preset or custom). `fit: width` fills the width
  and scrolls down, `page` shows the whole page, `actual` draws it at its own
  size. Items are absolutely positioned at `geometry × scale`; their content is
  laid out at native size (not a CSS transform), so text stays crisp and charts
  resize their own canvas. At a 1,200 px wide frame an upcast schema-2 page is
  drawn one to one.
- Array order is stacking order (`z-index`); DOM order is the tab order
  (`tab_order`, else reading order), so keyboard and screen-reader order are
  independent of stacking. Decorative items are `aria-hidden` in View. Hidden
  items are omitted in View and dimmed in Edit.
- Shapes are per-item SVG drawn in screen pixels; connectors are SVG paths on the
  page's coordinate system at their own stacking slot, joining element sides or
  points. A connector with a missing end, an end on a data point, an annotation
  and a `ValueBinding` run are not resolved yet: the first three are not drawn in
  View and a value shows a dash (fail closed, no guessed position or value).
- Below 520 px the page becomes one column: content items (also inside groups)
  in `mobile.order`, then reading order, minus `mobile.hidden`, heights clamped
  to 120–640 px. Editing is disabled there.
- Edit: pointer drag (3 px threshold) and eight resize handles. Top-level edges
  snap magnetically (12 px) to column starts and ends, otherwise to the baseline
  from the margin; heights leave a gutter, so a legacy cell snaps back to
  exactly itself. Alt/⌘ suspends snapping; Escape cancels. One
  `elements.arrange` is committed per gesture, with the element's rotation.
  Keyboard: Alt+arrows move (next column start, or one baseline row),
  Alt+Shift+arrows resize. Locked items do not move. Overlap is allowed.
- Corner radii are drawn on the theme scale (`themeRadius`): 8 page px is the
  theme's `--radius`, other values scale from it and 9999 is a pill, so an
  upcast card keeps the corner the grid editor drew in every theme.
- `legacy_spacers` are drawn as empty frames above the page's items, in saved
  order, as the grid editor drew them. They are `aria-hidden` in View, and can
  be selected, moved and resized in Edit; phones omit them.
- Studio's artifact panel mounts the same `DashboardEditor`, so it uses this
  renderer with no Studio change.

### Authoring (P2, part 1)

Pure geometry is in `authoring.ts`, editor commands in `commands.ts`; each
command returns one operation and a sentence for the screen-reader announcement.

- Selection: a click selects, Shift/Ctrl/⌘ toggles, a drag on empty page draws a
  marquee that selects what it touches in the open scope, Ctrl/⌘+A selects all
  and Escape clears. Double-clicking a group opens it as the scope. Selected
  siblings that are not locked move together; handles show for one item.
- Snapping: edges and centres snap (12 page px) to other top-level items' edges
  and centres, the page centre and margins (`grid.snap_to_elements`), and to
  column edges (`grid.snap`); an axis with no hit falls to the baseline. Guides
  are drawn for item and page lines (columns are already visible) with gap labels
  to the nearest neighbour on each side. Alt/⌘ suspends snapping; nothing snaps
  inside a group.
- Commands: align (several to their bounds, one to the page or its group),
  distribute (three or more, outer ones fixed), Auto-arrange (content kinds onto
  whole columns and rows in reading order around locked items; refused when a
  fixed-height page would overflow), restack, group/ungroup, lock/hide toggles,
  duplicate (new IDs, copied connectors rejoin copied ends) and delete. Grid
  show/snap toggles and the page settings dialog commit `page.canvas.set`.
- `elements.group`/`elements.ungroup` are server operations, not remove + add,
  because removing elements cascades into interactions, tab and phone order.
  Grouping keeps screen positions and stacks the group at the topmost member;
  ungrouping keeps positions and carries the group's hidden/locked flags.
- Undo records the inverse of every canvas operation; a removal is undone by
  re-adding each item above its former neighbour and restoring tab order, phone
  order and interactions. Removing only a group's child cannot be put back in
  place: the announcement says so and the undo history is cleared, so Undo
  never skips over it.
- Performance: pointer moves are coalesced into one animation frame. A move on
  the page translates the moving nodes (`translate`) instead of re-rendering
  them, and the drag view (guides, live boxes) lives in a small external store,
  so a frame re-renders only the overlay and lines, not the canvas; the nodes
  keep their translation until the moved page renders. A scrolling page grows
  in 120 px steps to follow a drag downwards. Resizes and moves inside a group
  re-render the affected items only.

### Authoring (P2, part 2)

Item creation and formatting build schema-3 operations in `insert.ts`; the
toolbar menus are in `insert-menu.tsx` and the **Format and layers** panel in
`item-panel.tsx`.

- Shapes (rectangle, ellipse, pill, triangle, line) and icons (the closed lucide
  set in `icons.ts`) are added with `elements.add` at the first free spot on the
  grid that keeps a gutter from everything else, or the top-left margin. They
  are decorative. Labelling an icon makes it non-decorative in the same patch.
- **Connect with a line** joins exactly two items with a connector whose ends
  are attached to those items (`kind: element`), so it follows moves and
  resizes. The server already refuses ends that are not on the page.
- Formatting is one `elements.update` per change, and it merges into the item's
  `style`. Fill and outline colours are theme tokens, never raw colours. The
  panel also sets padding, text role, shape, icon, connector ending and path,
  and the decorative flag. Making items decorative also removes them from
  `tab_order`. With nothing selected, the panel sets the page background
  (`page.canvas.set`, solid or two-token gradient).
- **Layers** lists items front to back and restacks one step with
  `elements.reorder`. **Tab order** lists the non-decorative items and writes
  `page.tab_order.set`; **Follow the page** clears it back to reading order.
- Keyboard: Shift/Ctrl/⌘+Space adds the focused canvas item to the selection,
  and Tab keeps a multi-selection. Layer rows take Shift/Ctrl+Enter or Space to
  add to the selection and Alt+Up/Down to restack; they stop the editor's own
  Alt+arrow move.
- Charts, KPIs, tables and text still enter through `widget.add` (grid cells).
  On a page that does not scroll, the cell is chosen so the item lands inside
  the page, and when no free cell fits it goes to the top-left (`slotOnPage`).
  Untitled items show their actions in a corner overlay, not a header row, so
  short text boxes keep their words visible in Edit.
- A KPI value that does not fit its box is drawn in compact notation (175.2M)
  with the full value in the tooltip and for screen readers, never truncated.

## Consequences

- Readers always receive schema 3. Integrations may keep creating schema-2
  dashboards and sending grid changes; both are applied to the canvas.
- The Dataviz assistant tools and Studio publish keep using grid cells through
  `legacy_widgets` until P3 moves them to element operations.
- The editor draws pages with the frame renderer (P1). Elements a grid cannot
  express (shapes, icons, connectors, annotations) are drawn when an
  integration creates them; the editor groups and ungroups items (P2 part 1)
  and creates shapes, icons and connectors (P2 part 2). Images arrive in P2
  part 3. Annotations and data-point anchors are not authored yet.
- The metadata schema moves to 63. A shared development database must be
  upgraded by the operator procedure (maintenance, backup, migrator, then the
  application's configured schema) before this code runs there.

## Phases

- **P0** (this ADR): contract, upcaster, canvas operations, adapters, migration,
  golden and property tests, generated types.
- **P1**: frame renderer (page frame, scale to fit, absolute DOM elements, SVG
  vector layer), mobile reading-order projection, accessible reading order,
  GridStack removed, Studio artifact panel on the same renderer, screenshot parity
  with upcast schema-2 dashboards at 1,200 px (≤ 0.1 % pixel difference).
- **P2**: authoring UI. Part 1: selection and marquee, grid settings, snapping
  to the grid and to element edges and centres (Alt/⌘ suspends), smart guides
  with distances, align/distribute, Auto-arrange, z-order, group, lock, hide and
  keyboard shortcuts. Part 2: selection pane (layer and tab order), text, shape,
  icon and background tools, connectors. Part 3: images.
- **P3**: Neva on canvas operations with a ghost preview, per-batch Apply and a
  visible cursor.

## Acceptance evidence for P0

- `backend/tests/unit/test_native_viz_canvas.py`: golden upcasts of every
  `backend/tests/fixtures/viz_dashboards_v2/` fixture, exhaustive cell round
  trip, seeded property cases, schema-2 digests pinned from the pre-change code,
  contract limits and canvas operations.
- `frontend/src/features/native-dataviz/dashboard/upcast.test.ts`: the browser
  upcaster matches the server goldens and inverts exactly.
- `backend/tests/integration/test_metadata_viz.py`: PG17 upgrade from schema 62
  keeps schema-2 rows byte-identical, reads them upcast, writes the next edit as
  schema 3 and rejects a mismatched `schema_version`.

## Acceptance evidence for P1

- `frontend/src/features/native-dataviz/dashboard/frame.test.ts`: frame scale,
  reading, tab and phone order, snapping (a legacy cell snaps to itself),
  resize, clamp, keyboard nudge, connector paths, theme radius, unscaled
  padding and the `elements.arrange` inverse.
- `qa-editor.test.tsx`, `qa-editor-a11y.test.tsx`, `qa-canvas.test.tsx`,
  `editor.test.tsx` and `presence.test.tsx` run the editor on the frame:
  pointer drag and resize with eight handles, keyboard moves and their
  announcements, 150 items, phone column, tab order, scaled presence cursors and
  `legacy_spacers`.
- Screenshot parity against `main` (GridStack), with no tolerance: the five
  `viz_dashboards_v2` fixtures, upcast and drawn at 1,200 px in light and dark,
  are pixel-identical (10 of 10 images, 0 differing pixels). Live development
  dashboards captured at a 1,200 px canvas match except for 1–11 colour levels
  on the first card's title glyphs and corners (up to 0.24% of pixels, none
  above 16 levels); `main` draws that first grid item differently from the
  identical second one, while the frame draws both the same. Phone captures
  differ by at most 0.03% of pixels, none above 16 levels inside the canvas.

## Acceptance evidence for P2 part 1

- `authoring.test.ts`: smart snapping (edges, centres, page centre, columns
  without guide lines, free placement, resize edges, gap labels), align,
  distribute, restack, Auto-arrange (overlaps, locked obstacles, selected only,
  fixed-height refusal) and every command's undo round trip.
- `qa-authoring.test.tsx`: marquee and Shift selection, moving two items in one
  change with guides, snapping to another item, a line following a dragged item
  during and after the drag, and the drag frame budget (after one warm-up drag
  and a garbage collection): with
  60 items every frame after the first stays within 8 ms whether all 60 move
  together or one snaps against the other 59.
- `editor.test.tsx`: select all, group, undo and redo by shortcut; align, send
  to back and lock from the Arrange menu; grid toggles and page settings
  validation and undo.
- `backend/tests/unit/test_native_viz_canvas.py`: grouping keeps positions,
  stacking and interactions; refusals for nested, unknown and repeated members;
  ungrouping keeps hidden/locked.

## Acceptance evidence for P2 part 2

- `insert.test.ts`: free-spot placement with gutter clearance, shape, icon and
  connector creation, decorative flags leaving tab order, style merges, page
  background, layer and tab-order commands and their messages.
- `item-panel.test.tsx`: page gradient background, shape fill/outline
  colour/shadow/opacity validation, text style and padding, labelling an icon,
  layers by button and Alt+arrow, hide and lock, tab order and **Follow the page**.
- `editor.test.tsx`: a keyboard-only build (add a shape, format it, add to the
  selection from **Layers**, connect, restack with Alt+Down, undo), and untitled
  items keep their words at the top in Edit (fails at 41 px with the old header
  row).
- `model.test.ts`: `slotOnPage` keeps new items inside a fixed-size page.
- `qa-chart-browser.test.tsx`: a KPI too wide for its box shows 175.2M, with
  the full value in the tooltip and for screen readers, and no clipping. The
  test fails without the compact form.
- Reference scenario 10 (financial analysis: sidebar, logo, navigation, title,
  five KPIs, a bar chart and a table) was built live through the editor UI with
  clicks, typing and drags only: 63 steps, 51 s automated, 8.2 minutes by a
  keystroke-level estimate. Reference 9 needs a photo background and is built in
  part 3.
