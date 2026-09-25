---
name: nova-page-layout
description: Review or build Nova console pages with a persistent header and viewport-bounded content scrolling. Use for frontend route, page shell, card height, and scroll behavior changes.
---

# Nova page layout

Use the Home page (`frontend/src/features/dashboard/index.tsx`) and the shared
`Header`, `Main`, and `AuthenticatedLayout` components as the reference. Inspect
the routed page, including its loading, empty, error, and detail states.

## Required outcome

- Every console route has a visible application header with access to the main
  navigation and a visible page or view title. The title can sit in the header
  or at the top of the content, as it does on Home. A standalone surface such
  as Nova Studio may use its own header, but it must still name the current view.
- The page occupies the available viewport height. Long content never increases
  the height of the application shell or pushes the header out of view.
- Only the intended content region scrolls vertically. A simple page can use
  `Main scroll`; a multi-panel page uses `Main fixed` or `data-layout="fixed"`,
  with `overflow-hidden` on the outer frame and `min-h-0 flex-1 overflow-y-auto`
  on the content region. For a table, editor, or list card, keep its header and
  actions outside the card's `min-h-0 flex-1 overflow-auto` body.
- Avoid nested vertical scrollers for the same content. Put horizontal overflow
  on the table or code panel with `overflow-x-auto`, not on the whole page.
- Size through the flex chain (`min-h-0`, `min-w-0`, `flex-1`, `overflow-hidden`).
  Do not add fixed pixel or viewport-height values to make a page appear to fit.

## Verification

Trace the height chain from `SidebarInset` to the scroll owner; an inner
`overflow-auto` cannot work if an ancestor grows with content. Check the page
with short and long content at 320px and desktop width. Confirm the header
remains visible, the outer document does not scroll, the content or card does,
and no content is clipped. Check the assistant-open state where supported.
Review every route that renders a changed shared component.
