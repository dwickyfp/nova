"""Nova's Dataviz assistant: a platform agent nobody has to choose.

The Dataviz tab's assistant helps with the dashboards on screen
(docs/arch-19-studio-dataviz.md, D6). It is not one of the user's agents: like
the skill author, it is a configuration Nova assembles per turn and runs
through the same bounded ``AssistantLoop``. What it may read comes from the
caller, not from an agent grant:

* its Semantic Views are the views the Supernova Business Ontology binds to
  concepts the question names, then every other published view the active
  role can open, at most
  ``MAX_VIEWS``. Each is checked under the caller's role
  (``semantic_view_service.get_active_for_agent``); Ranger decides the rows;
* its tools are fixed: governed semantic queries, change diagnosis, metrics,
  inline charts, the ontology, and the native dashboard tools that read and
  change Dataviz dashboards as the caller (every change is approved by the
  user and committed as a new revision).

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
#: (``native_dataset_query``), so they match its charts.
#: Planner requirements the dashboard's own query satisfies.
DASHBOARD_ALIASES = {
    "semantic_query": "native_dataset_query",
    "semantic_view_query": "native_dataset_query",
    "diagnose_change": "native_dataset_query",
    "compute_metrics": "native_dataset_query",
}

MISSION = (
    "Nova's Dataviz assistant. Help people understand and change the Dataviz dashboards "
    "they have open in Dataviz, using only verified data their active role may read."
)

INSTRUCTIONS = """
Answer every data question so the reader knows, as far as verified data allows:
- What: the measure, with its business definition from the Supernova Business Ontology
  (ontology_explain) and its unit.
- Who: the people, customers, outlets, products or teams behind the number, with the top
  contributors when the question is about a total or a change.
- When: the exact period and grain of every number, and the comparison period for a change.
- Where: the regions, cities, channels or locations involved when the data has them.
- Why: for a rise or fall, compare the two periods and name the members that moved it most.
  Call these drivers or contributors; a breakdown shows where a change happened, it does not
  prove its cause.
- How: how the number is computed (fields, filters and source dataset).

How to work:
- With a dashboard open (its brief is below), native_dataset_query on its datasets is the only
  data source, and "this dashboard" is that one, so your numbers match its charts: who -> a
  person dimension (for example a salesman or employee column), where -> region, city or
  channel, when -> a date field with its grain. Without a dashboard open, use semantic_query.
- Build and edit dashboards with the native dashboard tools when asked: add charts, change a
  chart, move or remove widgets, filters.
- A ranking (who or where is highest) is a query with that dimension sorted by the measure,
  largest first. For a change, query the two periods and compare them member by member.
- Quote totals from a query that computes them; never add up rows yourself (a breakdown can be
  cut).
- Write every number exactly as the tool returned it, with its unit, in the reader's format
  (329.840 unit, Rp1.573.810.277,50), never rounded words like "1,57 miliar": answers are
  checked number by number.
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
    "Say which filters were applied to every number.",
    "Name a change's drivers as contributors, never as proven causes.",
]


async def _views_for_question(
    user: dict[str, Any], question: str
) -> list[str]:
    """Views the ontology binds to the approved concepts a question names."""
    if not question.strip():
        return []
    from app.modules.ontology.resolver import ConceptResolver

    try:
        resolution = await ConceptResolver().resolve(question, user)
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


async def scope_views(user: dict[str, Any], *, question: str = "") -> list[str]:
    """The Semantic Views this turn may use, most relevant first.

    Only views the caller's active role can open are returned.
    """
    from app.modules.intelligence.semantic_views import semantic_view_service

    readable: list[str] = []
    for view_id in await _views_for_question(user, question):
        if await semantic_view_service.get_active_for_agent(view_id, user):
            readable.append(view_id)
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
