# AI Monitoring design review

Scope: the new Monitoring > AI Monitoring page, its API, and navigation entry.
Direction: Nova's `DESIGN.md`, ENERGY 1 / RHYTHM 2 / MOTION 1. Antislop was
applied during implementation as requested. Browser evidence uses clearly named
test fixtures; production data comes exclusively from the monitoring endpoint.

## Hard gate

- R-02 PASS: new page copy contains no em dashes; summary wording names the action, model, date, or missing data directly.
- R-03 PASS: browser layout checks pass at 320px and 1280px, including an assistant panel; controls use a minimum 44px touch height.
- R-17 PASS: every production metric comes from the usage API; unknown token totals remain unavailable.
- R-18 PASS: no testimonials, avatars, or customer claims were added.
- R-23 PASS: the user requested the page and monitoring navigation; existing Nova components and icons supply the visual language.
- R-24 PASS: the new sidebar URL has a file route and a generated route-tree entry; the production build resolves it.
- R-25 PASS: browser tests calculate foreground and muted-text contrast against background, card, and summary surfaces in both themes, requiring 4.5:1; the chart bar requires 3:1.
- R-26 PASS: browser tests activate all four filters, both ranking types, clear filters, refresh, auto-refresh, pagination, and both disclosure controls.
- R-27 PASS: loading, empty, error, retry, administrator-denied, and partial-history states are exercised by browser tests.
- R-28 PASS: no generic FAQ was introduced; coverage text explains this dashboard's actual accounting boundaries.
- R-32 PASS: keyboard tests open the period menu with Enter, dismiss it with Escape, verify restored focus, then Tab to Source with a visible focus style.
- R-33 PASS: features were written directly in source with patches; no runtime CSS rewriting or script-generated UI edits.
- R-34 PASS: light/dark browser renders and contrast assertions pass; chart colors resolve against the active theme.
- R-35 PASS: the production build runs and the real component is exercised in Chromium; live-session verification limits are documented separately.
- R-36 PASS: no security, performance, billing, or completeness claims are invented; incomplete history has an explicit alert.
- R-37 PASS: design follows Nova's documented surfaces, typography, spacing, and chart tokens with declared dials.
- R-38 PASS: test fixtures remain in test code; there are no sample metrics or fictional events in the production page.

## Purpose gate

- R-01 PASS: no gradients or glows; the chart accent distinguishes measured daily usage.
- R-04 PASS: the refresh glyph denotes refresh and the sidebar trend glyph denotes monitoring; no generic AI feature icons.
- R-06 PASS: inherited console typography and tabular numbers support repeated scanning of counts.
- R-07 PASS: gridlines occur only inside the quantitative chart to aid value comparison.
- R-08 PASS: no decorative button arrows.
- R-09 PASS: no promotional badges or decorative status indicators.
- R-10 PASS: no glassmorphism.
- R-12 PASS: no new elevated card shadows; existing control styles are retained.
- R-13 PASS: no glow effects.
- R-14 PASS: a summary strip, trend chart, prose summary, rankings, and activity table each serve a different reading task.
- R-19 PASS: chart animation is disabled; hover/focus feedback follows MOTION 1.
- R-22 PASS: no illustrations or decorative assets.

## Liveliness

- Dials PASS: ENERGY 1 / RHYTHM 2 / MOTION 1 is recorded before the page implementation.
- Consistency PASS: quiet console surfaces, varied data sections, and static charts match the dials.
- Focal point PASS: reported-token total and the single-accent daily chart dominate the screenshot.
- Whitespace PASS: spacing separates filters, summary, trend, and drill-down information.
- Accent PASS: Nova's first chart token encodes daily total usage; numbers remain neutral.
- Identity PASS: shared page header, semantic surfaces, controls, typography, and activity vocabulary belong to the existing Nova console.
- Design read PASS: this is an operational usage dashboard for Nova administrators, following `DESIGN.md`.

## Craftsmanship and consistency

- C-1 PASS: color, hierarchy, typography, spacing, panels, and icon choices have written reasons in `ai-monitoring.md`.
- C-2 PASS: all added controls have verified behavior; disabled pagination accurately marks the boundaries.
- C-3 PASS: each section answers usage volume, trend, attribution, individual activity, or coverage.
- C-4 PASS: state tests, narrow/desktop layouts, assistant-panel simulation, themes, and keyboard tests cover the new component.
- C-5 PASS: production values derive from retained records and missing attribution is explicit.
- R-05 PASS: sections follow the user's monitoring questions, with summaries before attribution and history.
- R-11 PASS: standard Nova panel and control radii are retained; no pill-based redesign.
- R-15 PASS: actions are named Refresh, Clear filters, Previous, Next, and filter-specific names.
- R-16 PASS: no marketing buzzwords or unsupported claims.
- R-20 PASS: content is specific to Nova Studio, Smart coordinator/specialist runs, and AI SQL accounting.
- R-21 PASS: the existing light/dark theme system is used and both themes are tested.
- R-29 PASS: the page uses neutral semantic surfaces and one chart accent.
- R-30 PASS: no external product design was copied.
- R-31 PASS: major visual decisions and accounting tradeoffs are documented with their purposes.

## Verification boundary

This is a component/build delivery gate, not production acceptance. The running
local app rejected the documented default login and the CLI could not connect
to StarRocks. Live database totals and the authenticated application session
were not verified. See `ai-monitoring.md` for accounting and availability limits.
