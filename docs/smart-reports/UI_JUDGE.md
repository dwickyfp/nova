# Intelligence UI and Smart Report review

Reviewer: Codex acting as the requested LLM judge. This is a source- and
screenshot-based product review supported by Chromium interactions and server
assertions. It is not an independent human usability study or screen-reader audit.

## Evidence reviewed

The browser opens the real Studio shell through a local Vite server and exercises
an HTTP application connected to patched StarRocks/Ranger. Test identities and
rows are disposable fixtures. Light/dark screenshots cover 320, 390, 768 and
1280 pixels. A 640 × 450 CSS viewport checks the reflow equivalent of 200% browser
zoom on a 1280 × 900 display; it is not an OS-level zoom measurement.

The Intelligence UI displays a server-validated chart/table with Bandung 3960 and
Jakarta 1980 from real authorized SQL. The report journey independently verifies
Jakarta 1980.00 and Bandung 3960.00, including backend resolution of Ask Nova's
context reference. A separate actual provider run creates the private report from
a prompt. Scripted-provider streaming is kept separate from that model run.

## Findings and repairs

| Finding | Consequence | Repair and verification |
| --- | --- | --- |
| Duplicate keys for export/activity controls | Page changes duplicated Export buttons | Distinct component keys; browser page-switch/export journey |
| Send accepted input before history loaded | A late history response could erase the new turn and consent card | Gate Send/Enter on selected-thread readiness, retain typed text; delayed-history browser regression |
| Escape did not consistently close actions | Keyboard focus stayed in the menu | Explicit menu Escape handling and focus-return acceptance |
| Fixed viewport filter columns | Search field became too narrow in the editor/reflow | Container-based auto-fit columns; width assertion and screenshots |
| Print width differed from canvas width | Chart overflow and nearly empty trailing PDF page | Same 1200-pixel print/screen layout; re-rendered exports |
| Fixed PPTX clipping left tiny final segments | Extra slides with a few footer lines | Balanced page segments and aspect-ratio fit within slide bounds |
| Configured street tiles in offline export | Map snapshot could not complete | Smart Report uses the existing bundled map adapter; no external tiles |
| Decimal literal promotion | COALESCE lost low digits in an otherwise decimal calculation | Minimal literal precision; independent real-engine decimal regression |
| Model confused report filters and semantic named filters | Valid-looking proposal failed semantic validation | Explicit prompt/schema distinction; actual provider UI run passed |

## Review criteria

- **Data and trust:** exact values remain inspectable, filters and revision are
  visible, and failed/partial/mapping states cannot masquerade as complete data.
  The business assertions pass independently of visual judgment.
- **Navigation:** Reports, Artifacts and conversation context are distinguishable.
  Open Full consumes its token; a copied link starts independently. Explicit
  Publish keeps private edits separate from the published report.
- **Readability:** restrained Nova tokens, readable labels and data-first hierarchy.
  Mobile controls wrap; report and chat content scroll in their owning regions.
  Disabled pagination controls are intentionally subdued.
- **Interaction:** focusable native filters and selections, keyboard Open Full and
  action-menu behavior. Zoom/reflow and final export screenshots are checked again
  after the repairs above.
- **Exports:** all pages and exact spreadsheet values are retained. Long report
  pages may occupy multiple slides/pages. Images in PPTX are presentation snapshots,
  not editable spreadsheet charts.

## Limits and decision

The final release decision remains in STATUS.md until all repository gates and
delivery steps finish. Local browser success does not establish production load,
universal prompt accuracy, or conformance with every assistive technology. The
statistical insight detector reports evidence and method, not causation or forecasts.

## Recorded review decision

The final real-HTTP prompt/stream and expanded report journeys both passed in
193.60 seconds after the history-readiness repair. The tested report experience is accepted: data values,
context transfer, private/published separation, keyboard actions, deterministic
cross-filter/drill, legacy navigation and artifact mapping behave as asserted.
The source retains a focused keyboard regression test. Final repository release
acceptance remains separate in STATUS.md.

Reviewed fixture screenshots:

- [Report at 320 px](evidence/report-320.png): readable KPI, wrapped toolbar,
  visible state and no horizontal overflow.
- [200% equivalent reflow](evidence/report-zoom.png): usable filter input and
  returned keyboard focus; content continues in the report scroll region.
- [Artifact mapping](evidence/artifact-mapping.png): the missing mapping is explicit,
  available widgets still render, and the private draft is identified.
- [Intelligence desktop](evidence/intelligence-desktop.png): chart and exact table
  agree; the table remains readable beside the workflow panel.
- [Intelligence at 390 px](evidence/intelligence-390.png): chart labels and table
  controls fit. Long conversation content must scroll above the fixed composer;
  the expanded browser check also verifies that behavior.
- [Scrolled Intelligence table](evidence/intelligence-390-scrolled.png): both exact
  rows remain visible above the composer at 390 px; the last row does not overlap
  the input or routing controls.

The empty workflow panel and multi-line mobile composer use existing Studio
behavior. They reduce the visible data area on small screens but do not change
report numbers or context. They are not evidence of a screen-reader audit.
