# Architecture 19: Studio Agent + Dataviz

> Status: **accepted; implementation in progress on `feat/dataviz`.** This is
> the technical decision record for the Agent / Dataviz split of Nova Studio.
> It refines [Architecture 17](arch-17-superset.md) (Superset as Studio's BI
> surface) and links every Dataviz context and action to the
> [Supernova Business Ontology](arch-18-business-ontology.md). Requirement IDs
> (`UX-`, `ART-`, `AI-`, `OPS-`) come from the product package
> *NOVA STUDIO — Agent + Dataviz, v1.0 (2026-11-08)*; the delivery report at the
> end of this file traces each one to code and tests.

Studio has two top-level tabs. **Agent** is the existing native chat: one
selected agent per turn, the shared bounded `AssistantLoop`, native charts,
maps, tables and Intelligent UI. **Dataviz** is the full authenticated Superset
application at same-origin `/bi/`, with a collapsible Nova assistant on the
right. Superset stays the only BI engine; Nova owns identity, the active role,
data authorization, provenance, ontology meaning, audit and the one publishing
pipeline both tabs use.

## Inventory before change (M0)

Verified in code on 2026-11-08 at `7261701`:

| Area | Found | Consequence |
| --- | --- | --- |
| Studio route | `/studio` search has `view`, `thread`, `agent`; no tab state | Add `tab` and a sanitized `bi` path; old URLs keep working |
| Full BI host | `features/bi/bi-workspace.tsx`, `bi-frame.tsx`, `use-bi-session.ts`; `/analytics` page frames `/bi/` | Reused as the Dataviz main pane |
| Save on result cards | `studio-chat.tsx` `saveArtifact` → `POST /studio/artifacts`; SQL from `artifact-source.ts` `findArtifactSql`, which **walks back through earlier turns** when the exact producer is missing | GAP-P0-02: retired from the UI; publication takes server-verified refs only |
| Silent table fallback | `superset/artifacts.py` `_chart_payload` catches `ValueError` and draws a table; `plan_from_vega` returns a table for unknown marks; the chat saves an unexportable chart "as a grid" | GAP-P0-03: the Add pipeline has no fallback; legacy edit paths keep theirs |
| Maps saved as tables | `geo-map.tsx`, `route-stops-map.tsx` save `draft.kind = "table"` | GAP-P0-04: maps are typed as maps and never offer Add |
| Dashboard membership | `create_dashboard_object` sends `PUT /chart/{id} {"dashboards": [id]}`, which **replaces** a chart's memberships | Fixed additively in the new pipeline |
| Existing dashboards | No code path appends to one; `_position` builds a new layout | New additive layout merge |
| Shared dashboards | `open_shared_dashboard` returns `viz_spec`; BI dashboards have none, so `SharedDashboardView` shows "no charts" | GAP-P0-05: superseded by D8, Dataviz dashboards are not shared; they belong to a Nova role |
| `dashboard_build` | Uses `context.last_result` SQL for every chart | GAP-P1-06: charts name the result they draw |
| Durable producer record | `CONFIG_ASSISTANT_MESSAGES.steps` keeps each tool step (`tool_call_id`, `evidence_id`, `preview` = executed SQL) and each table/chart/ui block with its `tool_call_id` or `provenance.tool_calls` | The basis of a verifiable `preview_ref` |
| Ontology on results | `semantic_query` builds `business_context` (release, concepts, bindings) but it lived only in the in-memory evidence tracker | Persisted on the tool step so publications and Dataviz answers can carry it |
| Superset worker | `nova-superset-worker` stopped since 2026-11-07 13:19Z while `superset-beat` kept enqueuing: **1 323 queued `reports.scheduler` ticks** in shared Redis db 6, none with `expires` | OPS finding, see [Lean deployment](#lean-deployment) |

## Decisions

### D1. A preview reference is a signed pointer to Nova's own record

`preview_ref` is an opaque, HMAC-signed locator
`{thread, message, content, node?}` bound to the caller's principal. The server
never trusts what the client says a chart contains. It reloads the persisted
assistant message **owned by the caller**, finds the block by `content_id`,
and takes:

- the SQL only from the producing tool step named by the block's
  `tool_call_id` (or the UI block's `provenance.tool_calls[evidence_id]`), or
  from a statement recorded on the block itself; never from another turn;
- the visualization from the stored block (`chart_spec`, `viz_spec`, or the
  OpenUI program node that binds `@Evidence(id)`);
- the semantic and ontology evidence persisted on that tool step.

A ref with no exact producer resolves to `UNKNOWN / NO_PROVENANCE`. Refs never
carry SQL, rows or credentials, and are useless to another principal (signature
and owner check). Data is re-authorized at publish time under the caller's
current role. Durable resolution means an old conversation keeps working; there
is no TTL store to expire.

### D2. One deterministic capability resolver

`backend/app/modules/superset/capability.py` returns
`SUPPORTED | CONVERTIBLE | UNSUPPORTED | UNKNOWN` with a stable `reason_code`,
`provider_version` and ranked alternatives (`preserved`, `lost`,
`business_meaning_preserved`, `requires_consent`). It is the only judge for the
Agent card button, the Dataviz assistant tools and the publish endpoint, so the
UI and the AI cannot disagree. No model is called.

- `SUPPORTED` needs exact provenance, a chart kind whose Superset viz type is
  in the registry pinned to 6.1.0 (`charts._VIZ_TYPES`, checked against the
  installed frontend bundle), fields present in the producing result, and no
  derivation Superset cannot reproduce (an OpenUI chart over `@Sort` only is
  reproducible; `@Filter`, `@GroupBy`, `@Top`, computed columns are not).
- Maps (`GeoMap`, route maps) are `UNSUPPORTED / INTERACTION_NOT_REPRESENTABLE`
  for direct publication. Alternatives are offered only when they keep the
  business question: a location table with the measure keeps location and
  metric but loses the map, the route order and the road geometry; a route map
  has no point-map alternative that keeps its meaning.
- An unknown or failing Superset is `UNKNOWN`, never `SUPPORTED`.
- `may_publish` (BI author role) is computed separately and never shown as a
  badge. Unsupported, convertible and unknown items show **no** Add control.

### D3. Publishing is a saga with a durable journal

`POST /api/v1/studio/dataviz/publish` runs, as the caller's own BI session:
resolve ref → capability → target check (existing dashboard the caller may
edit, or a new one) → dataset reuse (same owner and statement fingerprint) or
creation → strict chart plan → chart data API validation → chart reuse or
creation → **additive** dashboard layout merge and chart membership union →
read-back verification → artifact link and audit.

Each step is recorded in `NOVA_SYSTEM.CONFIG_DATAVIZ_PUBLICATIONS` (operation
id, idempotency key, request fingerprint, status, created object ids, revision
before/after, concept ids). Redis holds only a short per-dashboard lock. A
failure compensates exactly the objects this operation created and no longer
needs; anything already referenced elsewhere is never deleted; an operation
that cannot be proven either way is left `NEEDS_RECONCILIATION` and is visible
to its owner. Idempotency is scoped to principal + active role + security
context version + request fingerprint: a retry returns the first outcome, a
reused key with another payload is refused (409).

### D4. Conflicts with manual Superset edits

Superset 6.1.0 has no compare-and-swap on `position_json`. Its
`CONFIRM_DASHBOARD_DIFF` check compares only `css` and
`json_metadata.filter_scopes` (`OVERWRITE_INSPECT_FIELDS`), and the editor's
`PUT /api/v1/dashboard/<id>` carries no base revision. A Redis lock protects
Nova writers only. Nova therefore:

1. computes a dashboard revision (`changed_on` + hash of layout and metadata);
   an assistant change is previewed against it and applied only when it is
   unchanged (409 otherwise);
2. reads the framed editor's unsaved state through Superset's own contract:
   the dashboard registers a `beforeunload` guard that calls
   `preventDefault()` while it has unsaved changes (`useBeforeUnload` /
   `useUnsavedChangesPrompt`, present in the 6.1.0 bundle). Nova dispatches a
   synthetic, cancelable `beforeunload` on the same-origin frame and reads
   `defaultPrevented`; it never parses Superset's DOM. With unsaved changes,
   every write to that dashboard is refused until the user saves or discards;
3. merges additively at apply time: re-read, add nodes, keep every existing
   node, filter scope and membership, write, read back and verify;
4. reloads a clean Dataviz frame after a Nova write, so a later manual edit
   starts from the new layout.

A stale editor in **another** browser tab can still overwrite a Nova change
when it saves; Superset does not expose a base revision we can enforce without
patching its frontend. This is a stated limitation (see
[Limitations](#limitations)).

### D5. Dashboard context bridge, levels 1 and 2

- **Level 1:** the frame's same-origin path gives the dashboard id; the
  context service reads metadata, charts, datasets and owners through the
  caller's BI session.
- **Level 2:** active native filters come from Superset's official filter
  state API (`GET /api/v1/dashboard/<id>/filter_state/<native_filters_key>`)
  using the key in the frame URL. `filter_state_status` is `verified` when read
  this way, `unavailable` when there is no key, and `stale` when the key no
  longer resolves. URL-only state is never reported as verified. An analysis
  without verified filters says so.
- Selected chart and cross-filter selection are not exposed by Superset 6.1.0
  without a patch; they are `unavailable` (Level 3, P1).

The bridge accepts only messages from the frame it created (`event.source`),
same origin, schema version 1; it carries ids and filter keys, never secrets,
and it can never trigger a mutation.

### D6. One agent loop, ontology on every Dataviz context and action

**No agent to choose (decided 2026-11-08).** The Dataviz panel does not ask
the user to pick an agent: a result can come from any agent, and the panel's
job is Superset. It runs Nova's platform assistant
`nova-dataviz-assistant` (`backend/app/modules/agents/dataviz_assistant.py`),
assembled per turn like the skill author and run by the same
`AssistantLoop`. It has no agent grant to verify; it is admitted only on
conversation routes and its threads are the caller's own. Its Semantic Views
are chosen per turn, in this order and only when the active role can open
them: the views behind the open dashboard's datasets, the views the ontology
binds to concepts the question names, then other published views, at most 16.
Its tools are fixed: `semantic_query`, `diagnose_change`, `compute_metrics`,
`data_to_chart`, `ontology_resolve`, `ontology_explain` and the Dataviz tools.
Its instructions require answers that cover what, who, when, where, why and
how as far as verified data allows, name a change's drivers as contributors
rather than causes, and say which part cannot be answered. Deep, open-ended
analysis is handed to the Agent tab (**Continue in Agent**), where the user
picks an agent.

The request carries `dataviz` (dashboard id, filter key, edit state); the
server verifies it with the caller's BI session. The schema-bound Dataviz tools
are `dataviz_context`, `dataviz_chart_source` and `dataviz_query` (read-only;
`dataviz_query` answers from the dashboard's own datasets with its verified
filters applied, and ranks contributions between two periods),
`dataviz_list_results` and `dataviz_check_result` (resolver),
`dataviz_plan_add` (no mutation; returns a diff and an expiring action token)
and `dataviz_apply` (consent, `session_change`). There is no arbitrary
Superset URL tool.

Ontology linkage:

- **Context.** Each chart's dataset is matched to a Semantic View through
  `CONFIG_SEMANTIC_BI_DATASETS`; each metric the chart uses is matched to the
  approved concept bound to that view metric (ontology `representations`, only
  for views the caller can see). Chart and dashboard titles are also resolved
  lexically by `ConceptResolver`; those matches are labelled as name matches,
  not bindings.
- **Publication.** The `business_context` of the producing `semantic_query`
  (release id, manifest hash, concept ids, revisions, bindings) is persisted on
  the tool step, carried by the preview, stored on the publication journal and
  artifact link, written into the Superset chart's description as the concept
  definitions, and audited.
- **Answers.** "Data dari mana" names dashboard → chart → dataset → Semantic
  View → metric → concept (definition, release), and states each missing link.

**Numbers come from the dashboard on screen.** With a dashboard open that has
datasets, the router withdraws `semantic_query`, `diagnose_change` and
`compute_metrics` for the turn and maps them, when the planner still requires
them, to `dataviz_query` (`LoopContext.capability_aliases`). So every number
matches the charts and the dashboard's verified filters. `dataviz_query` and
`dataviz_context` are always in reach on such a turn, whatever the planner
chose. The brief's verified ontology definitions settle `ontology_resolve` and
`ontology_explain`; a dashboard-dataset answer whose facts carry the bound
concepts settles `ontology_resolve` too.

`dataviz_query` reads what a model writes:

- A business word becomes the one column whose name or description names it
  (`salesman` → `employee_name`).
- Periods are accepted as `A : B`, `A:B`, `A to B` or a month `YYYY-MM`; a
  comparison period that names no dates is dropped and reported.
- A time dimension uses the month grain unless another grain is asked for.
- A comparison is broken down by one non-time dimension at a time.
- Values are rounded to two decimals, the precision an answer quotes.

Its comparison table names each value column by its period
(`net revenue 2026-10`), so the engine's number guard can verify a sentence
such as "net revenue naik dari X menjadi Y". The table also carries an exact
Total row, `row_order`, `largest_in_period` and `top_contributors`. The
answer engine never ranks a row labelled Total as a member. Panel answers are
text and tables: the dashboard beside them is the visual.

**Adding what the assistant found.** `dataviz_query` records its resolved
query on its tool step (`trace_detail.dataviz_source`: dataset, saved metrics
or column aggregates, dimensions, period, grain, concepts; never sent to the
model).

- The resolver turns that record into a preview with no SQL. The capability
  check supports it as a table drawn **on the same dataset**: saved metrics by
  name, and the question's period as a `TEMPORAL_RANGE` filter.
- The dashboard's own filters are not fixed into the chart, because they
  already apply to every chart on it.
- A two-period comparison is refused (`COMPUTED_COMPARISON`): Nova computed
  it from two queries, and a Superset chart shows one period.
- The saga creates no dataset and no SQL-backed Studio record for such a
  chart.

A change prepared in one turn is remembered for its conversation (Redis, the
action's 15-minute TTL). The next turn's prompt names it, so a "yes" applies
exactly it; consent is still asked, and the prompt shows the server's
description. A verified apply settles the preparation steps the planner listed
again.

### D7. Sharing (superseded by D8)

Sharing a BI dashboard through a Nova share was considered (the recipient opens
it in their own BI session, after the owner publishes it) and dropped by D8.
Legacy `viz_spec` dashboards keep their share and read-only view.

### D8. A Dataviz dashboard belongs to a Nova role, not to shares

Product decision (2026-11-08): Dataviz dashboards are not shared. A dashboard
belongs to the active Nova role of the session that created it, and everyone
working under that role sees it in Dataviz.

- Superset's own `DASHBOARD_RBAC` carries it. Each Nova mirror user holds its
  Superset role (NovaViewer, NovaAuthor or NovaAdmin) plus a permission-less
  role `Nova role <ROLE>` for its active Nova role; switching the active role
  swaps that role on the next request.
- `nova_superset.role_scope` attaches each newly created dashboard (Nova
  pipeline, Superset UI, copy) to its creator's `Nova role <ROLE>` and
  publishes it, so the role's members can open it at once. Its owners see it
  whatever their active role; only owners edit it. Nova's BI admin role is not
  Superset's `Admin`, so it does not see other roles' dashboards either
  (verified: an ACCOUNTADMIN session gets 404 for a retail_steward dashboard).
- The role scopes who sees the dashboard, never the data: each chart still
  runs as the viewer, under their own Ranger rows and masks.
- `POST /studio/shares` refuses Dataviz dashboards. Dashboards made before D8
  keep no role: a draft stays with its owners, and a published one is visible
  to every BI user, as Superset defines for dashboards without roles.

## Lean deployment

See [operations/superset-feature-inventory.md](operations/superset-feature-inventory.md),
[operations/superset-lean-deployment.md](operations/superset-lean-deployment.md)
and [benchmarks/superset-lean/](benchmarks/superset-lean/).

## Limitations

- **Manual edits elsewhere.** Superset's editor saves a whole layout with no
  base revision. A stale editor in another tab can still overwrite a change
  Nova made. Nova detects unsaved edits in its own frame, checks the revision
  before applying, and never overwrites. It cannot stop another tab from doing
  so.
- **Charts the bridge sees.** The bridge knows the dashboard, its datasets
  and its verified native filters. It does not know the chart a user has
  selected or cross-filters (P1).
- **Dataset reuse.** A result from the Agent tab is published on its own
  `nova_pub_*` dataset. It does not reuse the Semantic View's synced dataset
  (P1). Results from the Dataviz assistant do use the dashboard's dataset.
- **Chart data latency.** Chart data takes about 1 s even when cached: every
  request through Nova's proxy runs SQLAlchemy's dialect probes (F4 in the
  lean deployment notes). Documented, not changed.
- **Role-scoped dashboards (D8).** Dashboards made before D8 have no role.
  Nova's BI admin role does not see other roles' dashboards. NovaViewer
  inherits Superset `Gamma`, which can create dashboards; that predates this
  work.
- **Tables.** Studio result tables show raw numbers for every agent, not only
  Dataviz.
- **Quality bar.** It was measured on 8 questions over one dashboard in
  Indonesian ([results](benchmarks/dataviz-assistant/README.md)). Answer time
  is about 50 s at the median, set by the model provider.

