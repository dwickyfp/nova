# Nova page layout audit

The Home page is the reference: a header sits above a viewport-bounded main
area, and long content scrolls inside that area. This audit covers the routed
pages as of 2026-09-25. Redirect-only routes have no rendered page.

| Route | Header | Scroll owner after review |
|---|---|---|
| `/` Home | Existing `Header` | Fixed `Main` inner scroller |
| `/workspaces` | Existing `Header` | Fixed workspace panes and their content scrollers |
| `/database-explorer` | Existing `Header` | Fixed explorer tree and detail scrollers |
| `/tasks` | Existing route `Header` | Fixed `Main` inner monitoring scroller |
| `/tasks/$graphId` | Existing `Header` | Fixed graph canvas and run history drawer |
| `/agents` | Existing `Header` | Bounded `Main scroll` |
| `/agents/$agentId` | Existing `Header` in loading and ready states | Bounded `Main scroll` |
| `/agents/skills` | Existing `Header` | Bounded `Main scroll` |
| `/agents/tools` | Existing `Header` | Bounded `Main scroll` |
| `/semantic-views` | Added `Header` | Bounded `Main scroll` |
| `/semantic-views/$viewId` | Shared `Header`, named detail heading | Bounded `Main scroll`; detail sections stay inside the page |
| `/semantic-views/builder` | Existing `Header` | Bounded `Main scroll` |
| `/ai-search` | Added `Header` | Bounded `Main scroll` |
| `/entities` | Added `Header` | Bounded `Main scroll` |
| `/feature-store` | Added `Header` | Bounded `Main scroll` |
| `/ml-models` | Existing `Header` | Bounded `Main scroll` |
| `/ai-providers` | Existing `Header` | Bounded `Main scroll` |
| `/studio` | Added named view header | Standalone viewport with content pane scrollers |
| `/stages` | Added route `Header`, existing page title | Fixed `Main`; stage and file tables scroll inside their cards |
| `/functions` | Existing route `Header` | Bounded `Main scroll` |
| `/external-catalogs` | Existing `Header` | Fixed page; catalog table scrolls inside its card |
| `/migration` | Existing `Header` | Fixed page with inner content scroller |
| `/users` | Existing `Header` | Bounded `Main scroll` |
| `/users/$username` | Existing `Header` | Bounded `Main scroll` |
| `/roles` | Existing `Header` | Bounded `Main scroll` |
| `/roles/$name` | Existing `Header` | Bounded `Main scroll` |
| `/access-control` | Existing `Header` | Bounded `Main scroll` |
| `/settings` | Existing `Header` | Bounded `Main scroll` |
| `/query-history` | Existing route `Header` | Fixed `Main` inner monitoring scroller |
| `/active-query` | Existing route `Header` | Fixed `Main` inner monitoring scroller |
| `/query-cost` | Existing route `Header` | Fixed `Main` inner monitoring scroller |
| `/monitoring/health` | Shared monitoring `Header` | Fixed `Main` inner monitoring scroller |
| `/monitoring/audit` | Shared monitoring `Header` | Fixed `Main` inner monitoring scroller |
| `/monitoring/loads` | Shared monitoring `Header` | Fixed `Main` inner monitoring scroller |
| `/monitoring/cluster` | Shared monitoring `Header` | Fixed `Main` inner monitoring scroller |
| `/sign-in` | Visible brand and sign-in header | Viewport-bounded form scroller |

`/monitoring`, `/monitoring/active`, `/monitoring/tasks`, and the former
`/agents/semantic/*` and `/agents/studio` URLs redirect to the pages above.

## Verification targets

- Header remains visible while long content scrolls.
- Outer document and application shell retain viewport height.
- Table and editor content scroll inside their own bounded panel where present.
- Narrow viewports and the assistant-open state do not create a second page
  scroll or clip actions.
