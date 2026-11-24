"""Semantic query fixes from the Studio benchmark, round 1.

* Period comparisons the compiler could not run as written ("June and July",
  a whole month written as dates with month over month, week over week across
  many weeks, an ordering on the hidden period) failed every retry with "The
  semantic plan could not be compiled safely" (30 failures, all comparisons).
* "Median order value" was refused: no median measure existed.
* "Orders shipped to Bandung" read as status Shipped and city Bandung returned
  nothing; the city-only figure was the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.planning import (
    SemanticPlan,
    SemanticTime,
    harmonize_plan,
    harmonize_time,
    validate_plan,
)
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.agents.tools.semantic_query import _plan_error_message
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.unit.test_semantic_guidance_fallback import USER, setup_tool
from tests.unit.test_semantic_intelligence import sales_model

NOW = datetime(2026, 11, 24, 1, tzinfo=UTC)


def _compiles(plan: SemanticPlan) -> str:
    model = sales_model()
    assert validate_plan(model, plan) == []
    context = None
    if plan.time and plan.time.range:
        context = resolve_execution_time(plan.time.range, plan.time.compare, now=NOW,
                                         timezone="Asia/Jakarta")
    return SemanticCompiler().compile(model, plan, time_context=context).sql


def _time(range_value, compare, grain=None):
    return SemanticTime("order_date", grain=grain, range=range_value, compare=compare)


@pytest.mark.parametrize(("time", "expected"), [
    # "Bandingkan pendapatan Juni dan Juli 2025": the two months, each a group.
    (_time("2025-06-01..2025-07-31", "month_over_month"),
     _time("2025-06-01..2025-07-31", None, "month")),
    (_time("2025-06-01..2025-07-31", "previous_period"),
     _time("2025-06-01..2025-07-31", None, "month")),
    # July written as dates, month over month: July against June.
    (_time("2025-07-01..2025-07-31", "month_over_month"), _time("2025-07", "month_over_month")),
    # "Week over week" across many weeks: the weekly series.
    (_time("last_8_weeks", "week_over_week", "week"), _time("last_8_weeks", None, "week")),
    (_time("2025-06-01..2025-07-05", "week_over_week"),
     _time("2025-06-01..2025-07-05", None, "week")),
    # A range of another length: against the period just before it.
    (_time("last_30_days", "month_over_month"), _time("last_30_days", "previous_period")),
    # Already compilable comparisons are left as they are.
    (_time("2025-07", "month_over_month"), _time("2025-07", "month_over_month")),
    (_time("previous_week", "week_over_week"), _time("previous_week", "week_over_week")),
    (_time("2025-07", "year_over_year"), _time("2025-07", "year_over_year")),
])
def test_comparisons_are_put_in_a_compilable_shape(time, expected):
    assert harmonize_time(time) == expected
    _compiles(SemanticPlan(metrics=("total_revenue",), time=harmonize_time(time)))


def test_a_comparison_drops_an_ordering_on_the_hidden_period():
    plan = SemanticPlan(
        metrics=("total_revenue",), time=_time("2025-07", "month_over_month"),
        order_by=(SemanticPlan.from_dict({"metrics": ["total_revenue"], "order_by": [
            {"field": "order_date", "direction": "asc"}]}).order_by[0],),
    )
    with pytest.raises(Exception, match="not selected"):
        _compiles(plan)
    assert "comparison_period" in _compiles(harmonize_plan(plan))


def test_from_dict_harmonizes_a_generated_plan():
    plan = SemanticPlan.from_dict({"metrics": ["total_revenue"], "time": {
        "dimension": "order_date", "range": "2025-07-01..2025-07-31",
        "compare": "mom", "grain": None}})
    assert plan.time == _time("2025-07", "month_over_month")


def test_median_and_percentiles_of_a_metrics_records():
    model = sales_model()
    median = model.metric("median_of_total_revenue")
    assert median is not None and median.base_dataset == "orders"
    sql = _compiles(SemanticPlan(metrics=("median_of_total_revenue",), dimensions=("city",)))
    assert "PERCENTILE_CONT(CAST(`orders`.`amount` AS DOUBLE), 0.5)" in sql
    assert "AS `median_of_total_revenue`" in sql
    assert "0.9)" in _compiles(SemanticPlan(metrics=("p90_of_total_revenue",)))
    for name in ("median_of_unknown", "p0_of_total_revenue", "p100_of_total_revenue",
                 "mean_of_total_revenue"):
        assert model.metric(name) is None


def test_a_plan_failure_tells_the_person_what_to_do():
    assert "Name both periods" in _plan_error_message(
        "month_over_month is incompatible with semantic range '2025-06-01..2025-07-31'.")
    assert "sort" in _plan_error_message("Order field 'order_date' is not selected.")
    assert "not in this data" in _plan_error_message("Unknown metric 'stock'.")
    assert "safe query" in _plan_error_message("Something else.")
    for message in (_plan_error_message("Unknown dimension 'city'."),
                    _plan_error_message("x")):
        assert "semantic plan" not in message.lower()


async def test_an_empty_combination_answers_without_the_filter_that_emptied_it(monkeypatch):
    from app.modules.query.service import query_service

    plan = {
        "metrics": ["total_revenue"], "dimensions": [], "named_filters": [], "order_by": [],
        "limit": None, "unresolved_concepts": [], "time": None,
        "filters": [{"field": "orders.city", "operator": "=", "value": "Bandung"},
                    {"field": "orders.region_id", "operator": "=", "value": "shipped"}],
    }
    tool, _planner, _execute = setup_tool(monkeypatch, plan)
    statements: list[str] = []

    async def execute(**kwargs):
        sql = kwargs["sql"]
        statements.append(sql)
        empty = "'shipped'" in sql and "'Bandung'" in sql
        rows = [[None]] if empty else [[22298000]]
        if "MIN(" in sql or "MAX(" in sql or "ORDER BY" in sql:
            rows = [["2025-06-01", 1]]
            return [SimpleNamespace(columns=["order_date", "total_revenue"], rows=rows,
                                    error=None)]
        return [SimpleNamespace(columns=["total_revenue"], rows=rows, error=None)]

    monkeypatch.setattr(query_service, "execute_statements", AsyncMock(side_effect=execute))
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(script=[
        tool_call_frame("s1", name="semantic_query",
                        arguments={"question": "revenue for city Bandung and region_id shipped"}),
        text_frame("No order is both shipped and in Bandung. For Bandung: 22,298,000."),
    ])
    context = LoopContext(user_name="alice", user=USER.copy())
    frames = [
        frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="relax", user_name="alice", title="Eval"),
            user_content="Revenue of orders shipped to Bandung?", context=context,
            resolve_consent=allow,
        )
    ]
    joined = "".join(frames)
    assert '"finish_reason":"stop"' in joined.replace(" ", "")
    assert "22,298,000" in joined
    # The probe ran as the caller, and the answer's table is the figure without the filter.
    assert any("'shipped'" not in sql and "'Bandung'" in sql for sql in statements)
    assert context.last_result["rows"] == [[22298000]]
    assert "'shipped'" not in context.last_result["sql"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Show the finance.ledger amounts for Bandung.", ["finance.ledger"]),
        ("Total amount di tabel finance.ledger semua kota berapa?", ["finance.ledger"]),
        ("Revenue from analytics.sales", []),  # the bound source's schema
        ("Revenue from analytics.sales.orders by city", []),  # the bound source
        ("Revenue from sales.orders", []),  # part of the bound source
        ("Group orders.city by revenue", []),  # a field of the model
        ("Revenue in Jakarta.Then by month, e.g. June", []),
        ("Revenue for 1.5 times the target", []),
        ("Mail ops@example.com the revenue", []),
    ],
)
def test_a_named_source_the_agent_cannot_use_is_reported(question, expected):
    from app.modules.agents.tools.semantic_query import unavailable_sources
    from tests.unit.test_semantic_intelligence import sales_model

    assert unavailable_sources(question, sales_model()) == expected


async def test_the_answer_is_told_to_say_the_named_source_is_unavailable(monkeypatch):
    from app.modules.assistant.service import LoopContext
    from app.modules.assistant.tools import ToolInvocation

    plan = {
        "metrics": ["total_revenue"], "dimensions": [], "named_filters": [], "order_by": [],
        "limit": None, "unresolved_concepts": [], "time": None,
        "filters": [{"field": "orders.city", "operator": "=", "value": "Bandung"}],
    }
    tool, _planner, _execute = setup_tool(monkeypatch, plan)
    context = LoopContext(user_name="alice", user=USER.copy())
    context.user_question = "Show the finance.ledger amounts for Bandung."
    outcome = await tool.run(
        ToolInvocation(tool_call_id="g1", tool_name="semantic_query",
                       arguments={"question": "revenue for city Bandung"}),
        context,
    )
    assert outcome.ok
    assert "finance.ledger is not available here" in outcome.summary
    assert "labelled as from" in outcome.summary


def test_a_value_in_another_letter_case_takes_the_stored_spelling():
    # number-028/030: 'credit card' found no 'Credit Card' rows, and two more
    # queries ran to find the stored spelling.
    from dataclasses import replace as copy

    from app.modules.agents.semantic.model_planner import repair_literals
    from app.modules.agents.semantic.planning import SemanticFilter, SemanticPlan
    from tests.unit.test_semantic_intelligence import sales_model

    model = sales_model()
    model = copy(model, datasets=tuple(
        copy(dataset, fields=tuple(
            copy(field, sample_values=("Credit Card", "Bank Transfer"))
            if field.name == "region_id" else field for field in dataset.fields))
        for dataset in model.datasets))
    plan = SemanticPlan(metrics=("total_revenue",), filters=(
        SemanticFilter("orders.region_id", "=", "credit card"),
        SemanticFilter("city", "=", "Bandung"),
    ))
    repaired = repair_literals(model, plan, "Revenue from credit card orders in Bandung")
    assert repaired.filters == (
        SemanticFilter("orders.region_id", "=", "Credit Card"),
        SemanticFilter("city", "=", "Bandung"),
    )
    listed = SemanticPlan(metrics=("total_revenue",), filters=(
        SemanticFilter("orders.region_id", "IN", ["bank transfer", "Credit Card"]),))
    assert repair_literals(model, listed, "x").filters == (
        SemanticFilter("orders.region_id", "IN", ["Bank Transfer", "Credit Card"]),)


async def test_a_looked_up_value_in_another_letter_case_takes_the_stored_spelling(monkeypatch):
    from dataclasses import replace as copy

    import app.modules.agents.semantic.literals as literals
    from app.modules.agents.semantic.model_planner import ground_literals
    from app.modules.agents.semantic.planning import SemanticFilter, SemanticPlan
    from tests.unit.test_semantic_intelligence import sales_model

    model = sales_model()
    model = copy(model, datasets=tuple(
        copy(dataset, fields=tuple(
            copy(field, sample_values=(), search_strategy="exact") if field.name == "city"
            else field for field in dataset.fields))
        for dataset in model.datasets))

    async def lookup(_model, field, words, context):
        return ["Bank Transfer"]

    monkeypatch.setattr(literals, "field_values", lookup)
    plan = SemanticPlan(metrics=("total_revenue",), filters=(
        SemanticFilter("city", "=", "bank transfer"),))
    grounded = await ground_literals(model, plan, "orders paid by bank transfer", object())
    assert grounded.filters == (SemanticFilter("city", "=", "Bank Transfer"),)
