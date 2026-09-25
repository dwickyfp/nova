# Intelligence pages: UI/UX research and decisions

## Problem observed in Nova

The sidebar had separate URLs for AI Search, Semantic Views, Feature Store, and Entities, but all four routes rendered `IntelligencePage`. A second row of buttons changed local component state. The URL and browser history did not follow those buttons, so the sidebar and page could disagree. Each page also led with creation forms and technical terms before explaining the user's first task.

## Sources

- [Nielsen Norman Group: Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/) recommends showing common tasks first and deferring advanced controls until requested.
- [Nielsen Norman Group: Recognition Rather Than Recall](https://www.nngroup.com/articles/recognition-and-recall/) explains why visible choices and contextual cues reduce memory demands.
- [W3C WCAG 2.2: Headings and Labels](https://www.w3.org/WAI/WCAG22/Understanding/headings-and-labels.html) calls for headings and labels that describe their topic or purpose.
- [W3C WCAG 2.2: Consistent Navigation](https://www.w3.org/WAI/WCAG22/Understanding/consistent-navigation.html) supports stable navigation across related pages.
- [GOV.UK Design System: Question pages](https://design-system.service.gov.uk/patterns/question-pages/) recommends splitting complex tasks into manageable steps and making progress clear.
- [GOV.UK Design System: Back link](https://design-system.service.gov.uk/components/back-link/) provides a clear return path from a detail page.

## Applied design decisions

1. Sidebar destinations are pages. The in-page intelligence section switcher was removed; route changes now drive the content and browser history.
2. AI Search starts with the existing indexes and a search task. Index creation is a secondary, disclosed task. The mode names explain text matching and meaning matching.
3. Semantic Views starts with a three-step path: build, check, publish. The visual builder is the main action. YAML/JSON import and direct editing are disclosed as advanced tasks. Each list item opens its own `/semantic-views/:id` page; the list no longer expands a detail panel below itself. The detail page starts with a return link, title, availability summary, and question preview. Version history, direct editing, queries, and lifecycle actions sit in separate sections so users can focus on one task at a time.
4. Feature Store explains Entity, Feature View, and Feature Group in the order needed to use them. Prerequisites and empty states link to the next useful action.
5. Inputs and sections use descriptive visible labels, concise help near the action, and responsive layouts. Technical definition output stays available without taking permanent space from the builder.

## Review criteria

- A new user can tell what each page does and where to start without knowing Ossie or StarRocks terminology.
- Visiting a sidebar URL directly shows its own content; switching destination changes the URL.
- Advanced authoring remains available without dominating the initial view.
- Keyboard users can identify selected items, form controls, and expandable sections.
- The layout remains usable at a 320px viewport without horizontal page overflow.
