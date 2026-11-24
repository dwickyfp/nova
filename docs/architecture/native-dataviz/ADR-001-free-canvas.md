# ADR-001: Free-form dashboard canvas (dashboard schema 3)

- Status: accepted. P0 (contract and upcaster) is implemented; P1–P3 are open.
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

## Consequences

- Readers always receive schema 3. Integrations may keep creating schema-2
  dashboards and sending grid changes; both are applied to the canvas.
- The Dataviz assistant tools and Studio publish keep using grid cells through
  `legacy_widgets` until P3 moves them to element operations.
- Until P1, the editor still renders with GridStack by projecting each page to
  grid cells; elements a grid cannot show (shapes, icons, connectors, annotations,
  groups) cannot be created yet.
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
- **P2**: authoring UI: grid settings, snapping to the grid and to element edges
  and centres (Alt/⌘ suspends), smart guides with distances, align/distribute,
  Auto-arrange.
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
