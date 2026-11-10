"""A measure that is an approved definition is answered from the view that computes it.

"How many orders do we have?" names no glossary term, so the model picks a
view. When the planned metric's own name is an approved concept computed in
another readable view (``order_count`` is the approved Order count), the tool
sends the model there: its own next call names the view it will cite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.modules.agents.semantic.access as semantic_access
import app.modules.agents.tools.semantic_query as semantic_query
import app.modules.ontology.grounding as grounding_module
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.ontology.grounding import Grounding
from app.modules.ontology.resolver import Resolution
from tests.unit.test_semantic_llm_planner import _plan, _run, setup_tool


def grounded(view_id: str, terms: list[str]) -> Grounding:
    return Grounding(
        status="grounded",
        resolution=Resolution(status="resolved", terms={"con_orders": terms}),
        view_id=view_id,
    )


def plan_of(*metrics: str) -> SemanticPlan:
    return SemanticPlan.from_dict(_plan(metrics=list(metrics)))


READABLE = [{"semantic_model_id": "retail", "name": "retail_sales"},
            {"semantic_model_id": "bench", "name": "nova_business_360"}]


@pytest.mark.parametrize(("metrics", "found", "expected"), [
    # The metric's whole name is the approved concept, computed elsewhere.
    (("order_count",), grounded("retail", ["order_count"]), "retail_sales"),
    (("order_count",), grounded("retail", ["order count"]), "retail_sales"),
    # Only part of the name is a concept: not the same measure.
    (("net_revenue_target",), grounded("retail", ["net revenue"]), None),
    # Already the view that computes it, or no concept at all.
    (("order_count",), grounded("bench", ["order_count"]), None),
    (("order_count",), Grounding(status="none"), None),
])
async def test_only_a_whole_metric_name_moves_the_query(monkeypatch, metrics, found, expected):
    monkeypatch.setattr(grounding_module, "ground", AsyncMock(return_value=found))
    monkeypatch.setattr(semantic_access, "load_authorized_models", AsyncMock(return_value=READABLE))
    assert await semantic_query._official_view(plan_of(*metrics), "bench", object()) == expected


async def test_a_plan_without_metrics_stays(monkeypatch):
    ground = AsyncMock()
    monkeypatch.setattr(grounding_module, "ground", ground)
    plan = SemanticPlan.from_dict(_plan(metrics=[], dimensions=["city"]))
    assert await semantic_query._official_view(plan, "bench", object()) is None
    ground.assert_not_awaited()


async def test_the_model_is_sent_to_the_official_view_before_anything_runs(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch, _plan())
    monkeypatch.setattr(
        grounding_module, "ground", AsyncMock(return_value=Grounding(status="none")),
    )
    monkeypatch.setattr(semantic_query, "_official_view", AsyncMock(return_value="retail_sales"))
    outcome = await _run(tool, "How many orders do we have in total?")
    assert outcome.error_class == "OFFICIAL_VIEW_REQUIRED" and outcome.recoverable
    assert "semantic_view set to retail_sales" in outcome.safe_detail
    assert "name retail_sales as the source" in outcome.safe_detail
    assert outcome.repair_context == {"semantic_view": "retail_sales",
                                      "metrics": ["total_revenue"]}
    provider.complete.assert_awaited_once()
    execute.assert_not_awaited()


async def test_every_answer_names_the_view_that_answered(monkeypatch):
    tool, _provider, _execute = setup_tool(monkeypatch, _plan())
    outcome = await _run(tool, "Revenue")
    assert outcome.ok, outcome.safe_detail
    assert "source: sales (the Semantic View that answered)" in outcome.summary


async def test_a_question_the_glossary_grounds_is_never_moved(monkeypatch):
    tool, _provider, _execute = setup_tool(monkeypatch, _plan())
    official = AsyncMock(return_value="retail")
    monkeypatch.setattr(semantic_query, "_official_view", official)
    # No published glossary here: the question's grounding is not "none".
    outcome = await _run(tool, "Revenue")
    assert outcome.ok, outcome.safe_detail
    official.assert_not_awaited()
    assert "approved definition" not in outcome.summary
