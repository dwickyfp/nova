"""Nova's Dataviz assistant: a platform agent nobody has to choose.

The Dataviz tab's assistant helps with the dashboards on screen
(docs/arch-19-studio-dataviz.md, D6). It is not one of the user's agents: like
the skill author, it is a configuration Nova assembles per turn and runs
through the same bounded ``AssistantLoop``. What it may read comes from the
caller, not from an agent grant:

* its Semantic Views are the views behind the open dashboard's datasets, then
  the views the Supernova Business Ontology binds to concepts the question
  names, then every other published view the active role can open, at most
  ``MAX_VIEWS``. Each is checked under the caller's role
  (``semantic_view_service.get_active_for_agent``); Ranger decides the rows;
* its tools are fixed: governed semantic queries, change diagnosis, metrics,
  inline charts, the ontology, and the Dataviz tools.

Its instructions ask for answers that cover what, who, when, where, why and
how wherever the data supports it, and say plainly when a part cannot be
answered from verified data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DATAVIZ_ASSISTANT_ID = "nova-dataviz-assistant"
MAX_VIEWS = 16
TOOLS = (
    "semantic_query",
    "diagnose_change",
    "compute_metrics",
    "data_to_chart",
    "ontology_resolve",
    "ontology_explain",
)
#: With a dashboard open, numbers come only from its own datasets
#: (``dataviz_query``), so they match its charts and verified filters.
#: Planner requirements the dashboard's own query satisfies.
DASHBOARD_ALIASES = {
    "semantic_query": "dataviz_query",
    "semantic_view_query": "dataviz_query",
    "diagnose_change": "dataviz_query",
    "compute_metrics": "dataviz_query",
}

MISSION = (
    "Nova's Dataviz assistant. Help people understand and change the governed BI dashboards "
    "they have open in Dataviz, using only verified data their active role may read."
)

INSTRUCTIONS = """
Answer every data question so the reader knows, as far as verified data allows:
- What: the measure, with its business definition from the Supernova Business Ontology
  (the concepts in the dashboard brief, or ontology_explain) and its unit.
- Who: the people, customers, outlets, products or teams behind the number, with the top
  contributors when the question is about a total or a change.
- When: the exact period and grain of every number, and the comparison period for a change.
- Where: the regions, cities, channels or locations involved when the data has them.
- Why: for a rise or fall, compare the two periods and name the members that moved it most.
  Call these drivers or contributors; a breakdown shows where a change happened, it does not
  prove its cause.
- How: how the number is computed (formula, filters, source dataset and Semantic View) and,
  for a chart, its lineage (dataviz_chart_source).

How to work:
- The dashboard brief below lists its datasets, saved metrics, columns and verified filters.
  Answer from those datasets with dataviz_query so your numbers match the charts and the
  dashboard's filters: who -> a person dimension (for example a salesman or employee column),
  where -> region, city or channel, when -> period on the time column.
- Why/what changed: call dataviz_query with period, compare_period and one non-time dimension
  at a time (for example region, then brand or salesman); report the Total row's change and
  change_pct, then the two or three largest contributions with their share_of_change_pct,
  each member's numbers taken together from top_contributors.
- Quote totals from the Total row; never add up rows yourself (a breakdown can be cut). Name
  the row a change belongs to: "Total naik Rp81.598.302,97 (5,47%)", "Jawa Timur naik ...";
  a change stated without its row ("net revenue naik ...") cannot be verified and is removed.
- Write numbers in the reader's format: 329.840 unit, not 329840.0.
- With a dashboard open, dataviz_query is the only data source; without one, use semantic_query.
- A ranking (who or where is highest) is a dataviz_query with that dimension and no
  compare_period: rows come sorted by the first metric, largest first. Add compare_period only
  when the question asks about a change; then rows are ordered by the size of the change, and
  the largest members of the period are in largest_in_period.
- Write every number exactly as the tool returned it, with its unit (Rp1.573.810.277,50), never
  rounded words like "1,57 miliar" or "71 juta": answers are checked number by number.
- Answer in the language of the user's question, the whole answer in that one language.
- Lead with the direct answer in one or two sentences, then the 5W+1H points that apply, as a
  short list. Skip a W that does not apply instead of padding it. If a part cannot be answered
  from the data you can read, say which part and why. Never invent a number, a cause, a filter
  or a definition.
- For deep or open-ended investigation across many sources, suggest continuing in the Agent
  tab.
""".strip()

STANDING_RULES = [
    "State the period of every number.",
    "Say whether the dashboard's current filters were applied.",
    "Name a change's drivers as contributors, never as proven causes.",
]


async def _views_for_dashboard(user: dict[str, Any], dashboard_id: int | None) -> list[str]:
    """Semantic Views behind the open dashboard's datasets, read as the caller."""
    if not dashboard_id:
        return []
    from app.modules.superset.client import BiError, SupersetClient
    from app.modules.superset.dashboard_context import _semantic_views

    try:
        async with SupersetClient.for_user(user, author=False) as bi:
            body = await bi.request("GET", f"/api/v1/dashboard/{int(dashboard_id)}/datasets")
    except BiError:
        return []
    ids = [int(row["id"]) for row in body.get("result") or [] if row.get("id") is not None]
    from app.core.metadata_scope import actor_for

    mapped = await _semantic_views(ids, actor=actor_for(user))
    return list(dict.fromkeys(str(item["view_id"]) for item in mapped.values()))


async def _views_for_question(
    user: dict[str, Any], question: str, scope: list[str] | None = None
) -> list[str]:
    """Views the ontology binds to the approved concepts a question names,
    read in the business of ``scope`` (the open dashboard's views) when given."""
    if not question.strip():
        return []
    from app.modules.ontology.resolver import ConceptResolver

    try:
        resolution = await ConceptResolver().resolve(question, user, scope_views=scope or None)
    except Exception as exc:  # noqa: BLE001 - resolution helps scope, it never grants
        logger.info("Ontology scope unavailable: %s", type(exc).__name__)
        return []
    views: list[str] = []
    for bindings in resolution.bindings.values():
        for item in bindings:
            view_id = getattr(getattr(item, "semantic", None), "view_id", None)
            if view_id:
                views.append(str(view_id))
    return list(dict.fromkeys(views))


async def scope_views(
    user: dict[str, Any], *, dashboard_id: int | None = None, question: str = ""
) -> list[str]:
    """The Semantic Views this turn may use, most relevant first.

    Only views the caller's active role can open are returned; the order puts
    the dashboard's own views first, so its numbers and the answer agree.
    """
    from app.modules.intelligence.semantic_views import semantic_view_service

    dashboard = await _views_for_dashboard(user, dashboard_id)
    preferred = [*dashboard, *(await _views_for_question(user, question, dashboard))]
    readable: list[str] = []
    for view_id in dict.fromkeys(preferred):
        if await semantic_view_service.get_active_for_agent(view_id, user):
            readable.append(view_id)
    if dashboard and readable:
        # A dashboard on screen is the business of this turn: no other
        # business's views (or meanings of its terms) are mixed in.
        return readable[:MAX_VIEWS]
    if len(readable) < MAX_VIEWS:
        try:
            for record in await semantic_view_service.list_active_for_agent(user):
                view_id = str(record.get("id") or "")
                if view_id and view_id not in readable:
                    readable.append(view_id)
                if len(readable) >= MAX_VIEWS:
                    break
        except Exception as exc:  # noqa: BLE001 - fewer views, never other views
            logger.info("Role-visible views unavailable: %s", type(exc).__name__)
    return readable[:MAX_VIEWS]


def dataviz_assistant_config(
    owner_name: str,
    *,
    semantic_view_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The assistant as an agent record; never persisted."""
    stamp = datetime(2026, 11, 8)
    return {
        "agent_id": DATAVIZ_ASSISTANT_ID,
        "owner_name": owner_name,
        "name": "Nova Dataviz assistant",
        "description": MISSION,
        "default_tools": list(TOOLS),
        "default_skills": [],
        "discoverable_skills": [],
        "instructions_response": INSTRUCTIONS,
        "instructions_orchestration": "",
        "harness_mode": "auto",
        "harness_policy": {"standing_rules": STANDING_RULES},
        "budget_profile": "analyst",
        "budget_seconds": 300,
        "budget_tokens": None,
        "policy": "auto_read_only",
        "visibility": "private",
        "semantic_view_ids": list(semantic_view_ids or []),
        "semantic_model_ids": [],
        "semantic_model_id": None,
        "model_provider_id": None,
        "model_name": None,
        "resource_bindings": {"search_indexes": [], "feature_groups": []},
        "release_manifest_id": None,
        "sample_questions": [],
        "created_at": stamp,
        "updated_at": stamp,
    }


def is_dataviz_assistant(agent_id: str | None) -> bool:
    return agent_id == DATAVIZ_ASSISTANT_ID
