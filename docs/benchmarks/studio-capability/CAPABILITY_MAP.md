# Nova Studio capability map

> What a Studio agent turn can produce and do, as found in the code on
> 2026-11-23 (main at `71c1fb4f`), updated 2026-11-24 for F1–F5, maps, routes,
> pictures and MCP/skills (main at `8f217415`), and how the capability benchmark
> exercises it.

Sources: `backend/app/modules/agents/registry.py` (tool selection),
`router.py` (turn, HITL, cancel, replay, deep research routes),
`backend/app/modules/assistant/events_v2.py` (stream protocol),
`backend/app/modules/assistant/human_input.py` (cards),
`frontend/src/features/studio/runtime/components/layout.ts` (page components),
`frontend/src/features/studio/studio-composer.tsx` (`/` command) and the
Studio user docs under `frontend/src/features/docs/content/studio/`.

## Agent under test

"Sales Analyst" (owner alice, model `gpt-power`, budget profile `analyst`,
policy `auto_read_only`) is bound to the `demo_sales` Semantic View over
`NOVA_DEMO.orders`: metrics `revenue`, `order_count`, `average_order_value`;
dimensions `order_date`, `status`, `payment_method`, `shipping_city`,
`customer_id`, `order_id`. Its selected tools are `semantic_query`,
`data_to_chart` and `diagnose_change`.

## Answer types

| Answer | How it reaches the user | Benchmark categories |
| --- | --- | --- |
| Single number | Answer text (`text` part), or a `Kpi` component | `number`, `date_relative`, `typo` |
| Table | `table` part from a governed result, or `Table` component | `table`, `breakdown` |
| Chart: bar, line, area, pie/donut, scatter | `chart` part (Vega mirror from `data_to_chart`; chart types include stacked/side bars, bubble, heatmap) or `Chart` component (`bar`, `line`, `area`, `pie`, `donut`, `scatter`) | `chart_*`, `trend` |
| KPI tiles | `KpiTiles` component, or `Metric` tiles on a page | `kpi` |
| Map | `GeoMap` (governed, needs coordinates or geometry), or `DataMap` on a page; public places and road routes with distance, time and alternatives via page `RouteMap` | `map` |
| What-if / simulation | `compute_metrics`, and page `Slider`/`Segmented`/`Toggle` controls that recompute locally | `what_if` |
| Narrative / summary / insight | Answer text | `narrative` |
| Comparison, ranking, trend | Text + table/chart | `comparison`, `ranking`, `trend` |
| Diagnosis of a change | `diagnose_change` (exact two-period decomposition) | `diagnosis` |
| Anomaly | Text + table from ranked results (no dedicated tool) | `anomaly` |
| Forecast | No forecasting tool on this agent (`ml_execute` not selected); a stated limitation or a labelled naive projection | `forecast` |
| Multi-component composition | `ui` program with `Grid`/`Card`/`Kpi`/`Chart`/`Table` | `chart_composite` |
| Page (F5, `nova-layout-1`) | One `ui` part streamed statement by statement: `Page`, `Header`, `Section`, `KpiTiles`/`Metric`, `DatavizChart`, `ResultTable`; the first section is timed | `layout_page` |
| What-if controls | Page `Slider`, `Segmented`, `Toggle` over verified figures, recomputed in the browser | `what_if_slider` |
| Data map | Page `DataMap` → `GeoMap`; rows without coordinates are placed at the city they name | `data_map` |
| Public places and road routes | Page `RouteMap` → `PlaceMap`: checked places, up to three OSRM alternatives, multi-stop, `optimize` order, no route across islands (`unavailable`) | `place_map`, `route`, `route_multistop`, `route_overwater`, `route_optimize`, `session:route` |
| Pictures | Page `Image`/`SearchImage` → `Picture` (Wikimedia only; "Image unavailable" with a link when Commons fails) | `picture` |
| Chart draft and save suggestion (F4) | `artifact.draft` per chart draft and one `save.suggested`; saving happens only from the artifact panel (`artifact.saved` later) | `artifact_save`, `session:artifact` |
| Tool chips and sources | One chip per distinct tool under the answer, `citation` parts; follow-ups must not name tools | `chips` (plus checks on every turn) |
| Plain answer for a plain question | Text, optionally one KPI tile; no page, chart or map | `simple`, `session:simple` |

## Tools

| Tool | Available to Sales Analyst | Exercised by |
| --- | --- | --- |
| `semantic_query` | yes | every data question |
| `data_to_chart` | yes | chart categories |
| `diagnose_change` | yes | `diagnosis`, `session:why` |
| `compute_metrics` | yes (added with any data tool) | `what_if` |
| `schedule_automation` | yes (added with any data tool) | `automation`, `session:action` |
| `request_human_input` | yes (every Studio agent) | `ambiguous`, `session:hitl` |
| `load_skill` | Sales Analyst has no skills; the disposable fixture agent offers the uploaded `monthly-revenue-review` skill | `skill` |
| `describe_agent` | yes | `agent_meta` |
| Raw SQL (`query_execute`) | no: Studio's registry never registers it, even when selected | `governance` SQL requests (refusal expected) |
| MCP tools | none on Sales Analyst; the fixture agent binds the test service's `fx_convert`, `get_store_targets`, `fail_on_purpose`, `record_target_note` (tool parts are named `mcp_<tool id>`) | `mcp` |
| Native Dataviz write tools | only for the Dataviz assistant or a turn with a native dashboard on screen | `dataviz_save` (guidance or approval expected) |

## Human in the loop

`request_human_input` offers `question` (choices and/or free text), `choice`
(2–4 options, one may be recommended) and `form` (select, text, date,
date range, number, integer, toggle). Since #242 Studio runs tools without an
approval card (owner decision, 2026-11-23); the corpus therefore expects none, and an approval
card is scored as a defect (`approval_card_shown`). The runner answers through
the same route as the card: questions with the first choice, choices with the
recommended or first option, forms with the first value or the data's date
range, and it denies any approval card that still appears.

## Conversation and run controls

| Capability | Route / mechanism | Benchmark |
| --- | --- | --- |
| Follow-ups, context carry-over, corrections, topic switches | Same thread; earlier messages are the history | 38 sessions, 3–6 turns |
| Fresh vs session | Each session turn's `fresh` twin runs in a new thread | `fresh_twin` units |
| Reload / replay | `GET .../runs/{run_id}/events` replays the stored v2 journal | every turn (`replay` check) |
| Stop | `POST .../runs/{run_id}/cancel` | `stop`, `session:stop_retry` |
| Retry | The client resends the same question | `session:stop_retry` turn 2 |
| Reconsider | The client sends a fixed "Reconsider your previous answer…" prompt | not separately scored |
| Deep research | `POST /agents/{id}/deep-research` (own thread, plan, report) | `deep_research` |
| `/` commands | Only `/create-skill-with-chat` (skill authoring) | `slash` |
| Role switch | `role.changed` event; no approval card expected | `role_switch` |
| Languages | Indonesian and English questions, answer language checked | every unit, `language` |

## Governance

| Rule | Where it holds | Benchmark |
| --- | --- | --- |
| No access to `NOVA_CATALOG`, `NOVA_ANALYTICS` for `marketing` | Ranger (access denied for alice) | `governance` with leak markers |
| Row filter `analytics.sales` → Jakarta for alice | Ranger row filter | `governance-010/011` leak markers |
| Mask `analytics.customers.phone` | Ranger mask | `governance-012` leak markers |
| Tables outside the agent's view (`customers`, `order_items`) | Semantic binding; no raw SQL tool | `governance` (limitation expected) |
| Non-read-only SQL | Refused in Studio | `write_refusal` |
| Prompt injection asking for other data or roles | Same rules | `governance-014..018` |

## Known gaps found while mapping (not product fixes)

- Studio has no raw SQL tool: `query_execute` is accepted in an agent's
  `default_tools` but never registered by `build_registry`.
- Sales Analyst has no skills or MCP connectors bound. MCP and skill turns
  run on a disposable fixture agent (`fixtures.py`): Sales Analyst's model,
  bindings and tools plus the repository's MCP test service and skill,
  created before the run and deleted after it.
- The view labels amounts as USD while the seeded values are rupiah-sized
  (e.g. 23,298,000); answers that say "$23.3M" are formatted from the view's
  unit, not from the data.
- The view's `status` sample values list `cancelled`, which never occurs.
