"""A measure word several metrics fit is answered with its usual reading, stated.

The planner picks the reading people usually mean and names the others; the
answer says which one it used. Only a measure no metric fits is asked back,
and a request about how the result is shown never blocks the data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.coverage import covered_period
from app.modules.agents.semantic.ir import SemanticMetricIR
from app.modules.agents.semantic.model_planner import PLANNER_RULES
from app.modules.agents.semantic.plan_contract import validate_generated_plan
from app.modules.agents.semantic.planning import (
    SemanticPlan,
    SemanticPlanError,
    validate_plan,
)
from app.modules.agents.semantic.runtime import semantic_ir_to_definition
from tests.unit.test_semantic_intelligence import sales_model
from tests.unit.test_semantic_llm_planner import _plan, _run, setup_tool


def two_revenues():
    model = sales_model()
    gross = SemanticMetricIR(
        name="gross_revenue",
        expression="SUM(orders.amount)",
        base_dataset="orders",
        description="Revenue before discounts and returns",
        synonyms=("gross sales",),
        default_time_dimension="order_date",
    )
    return replace(model, metrics=(*model.metrics, gross))


SALES_READING = {"text": "sales", "metric": "total_revenue", "alternatives": ["gross_revenue"]}


def use_model(monkeypatch, tool, model):
    monkeypatch.setattr(tool, "_resolve_model", AsyncMock(return_value={
        "semantic_model_id": "sales", "definition": semantic_ir_to_definition(model),
    }))


def test_the_rules_answer_the_usual_reading_instead_of_asking():
    assert "plan the usual reading and add it to readings" in PLANNER_RULES
    assert "the net figure after discounts and returns" in PLANNER_RULES
    assert "never stretch an unrelated metric" in PLANNER_RULES
    assert "is never material" in PLANNER_RULES
    assert "type_hint 'data_period' and material false" in PLANNER_RULES


def test_a_reading_is_kept_in_the_plan_but_does_not_change_what_it_computes():
    plan = SemanticPlan.from_dict(_plan(readings=[SALES_READING]))
    assert plan.readings[0].metric == "total_revenue"
    assert plan.readings[0].alternatives == ("gross_revenue",)
    assert plan == SemanticPlan.from_dict(_plan())
    assert plan.as_dict()["readings"][0]["text"] == "sales"
    # A plan without a reading keeps the shape stored before readings existed.
    assert "readings" not in SemanticPlan.from_dict(_plan()).as_dict()


def test_a_plan_from_before_readings_still_passes_the_contract():
    stored = _plan(having=[], transforms=[], top_n_per_group=None)
    validate_generated_plan(stored)
    assert stored["readings"] == []


def test_a_reading_must_name_a_selected_metric_and_real_alternatives():
    model = two_revenues()
    wrong = SemanticPlan.from_dict(_plan(readings=[
        {"text": "sales", "metric": "gross_revenue", "alternatives": ["refunds"]},
    ]))
    errors = validate_plan(model, wrong)
    assert any("not a selected metric" in error for error in errors)
    assert any("Unknown alternative metric 'refunds'" in error for error in errors)
    assert validate_plan(model, SemanticPlan.from_dict(_plan(readings=[SALES_READING]))) == []


async def test_an_ambiguous_measure_is_answered_and_the_reading_is_stated(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch, _plan(readings=[SALES_READING]))
    use_model(monkeypatch, tool, two_revenues())
    outcome = await _run(tool, "Total sales")
    assert outcome.ok, outcome.safe_detail
    assert "'sales' was read as total_revenue (Governed completed-order revenue)" in (
        outcome.summary
    )
    assert "also available: gross_revenue (Revenue before discounts and returns)" in (
        outcome.summary
    )
    assert "offer the others" in outcome.summary
    assert outcome.data["semantic_plan"]["readings"][0]["metric"] == "total_revenue"
    assert provider.complete.await_count == 1
    assert execute.await_count >= 1


async def test_a_wrong_reading_is_repaired_not_answered(monkeypatch):
    bad = _plan(readings=[{"text": "sales", "metric": "gross_revenue", "alternatives": []}])
    tool, provider, execute = setup_tool(monkeypatch, bad, _plan(readings=[SALES_READING]))
    use_model(monkeypatch, tool, two_revenues())
    outcome = await _run(tool, "Total sales")
    assert outcome.ok, outcome.safe_detail
    assert provider.complete.await_count == 2
    assert "not a selected metric" in provider.complete.call_args.kwargs["messages"][-1][
        "content"
    ]


async def test_a_presentation_request_never_blocks_the_data(monkeypatch):
    extra = {"text": "the date range the data covers", "type_hint": "presentation",
             "material": False}
    tool, _provider, execute = setup_tool(monkeypatch, _plan(unresolved_concepts=[extra]))
    outcome = await _run(tool, "Total revenue, with the date range covered")
    assert outcome.ok, outcome.safe_detail
    assert "Not shown by this query: the date range the data covers." in outcome.summary
    assert execute.await_count >= 1


async def test_a_measure_no_metric_fits_is_still_asked_back(monkeypatch):
    missing = {"text": "payroll", "type_hint": "metric", "material": True}
    tool, _provider, execute = setup_tool(
        monkeypatch, _plan(metrics=[], dimensions=["city"], unresolved_concepts=[missing]),
    )
    outcome = await _run(tool, "Payroll per city")
    assert outcome.error_class == "CLARIFICATION_REQUIRED"
    execute.assert_not_awaited()


def _window_runner(first: str, last: str):
    calls: list[dict] = []

    async def run(window_plan):
        calls.append(window_plan)
        day = first if window_plan["order_by"][0]["direction"] == "asc" else last
        return ["order_date", "total_revenue"], [[day, 10]], None

    return run, calls


ASKS_PERIOD = {"text": "the dates the data covers", "type_hint": "data_period",
               "material": False}


async def test_the_dates_the_data_covers_are_read_when_asked():
    run, calls = _window_runner("2025-01-03", "2026-10-31")
    plan = SemanticPlan.from_dict(_plan(unresolved_concepts=[ASKS_PERIOD]))
    window = await covered_period(plan, sales_model(), run)
    assert (window.first, window.last) == (date(2025, 1, 3), date(2026, 10, 31))
    # Read through the metric's own time field, as two governed plans.
    assert [item["time"]["dimension"] for item in calls] == ["order_date", "order_date"]


async def test_an_answer_over_all_the_data_reads_its_dates_unasked():
    run, calls = _window_runner("2025-01-03", "2026-10-31")
    window = await covered_period(SemanticPlan.from_dict(_plan()), sales_model(), run)
    assert (window.first, window.last) == (date(2025, 1, 3), date(2026, 10, 31))
    assert len(calls) == 2


@pytest.mark.parametrize("plan", [
    # A dated question already names its period.
    _plan(unresolved_concepts=[ASKS_PERIOD], time={
        "dimension": "order_date", "grain": None, "range": "previous_month", "compare": None,
    }),
    _plan(time={
        "dimension": "order_date", "grain": None, "range": "previous_month", "compare": None,
    }),
    # Nothing to read the dates of.
    _plan(metrics=[], dimensions=["city"]),
])
async def test_the_dates_are_not_read_when_not_needed(plan):
    run, calls = _window_runner("2025-01-03", "2026-10-31")
    assert await covered_period(SemanticPlan.from_dict(plan), sales_model(), run) is None
    assert calls == []


async def test_the_answer_says_which_dates_the_data_covers_when_asked(monkeypatch):
    from app.modules.query.service import query_service

    tool, _provider, execute = setup_tool(
        monkeypatch, _plan(dimensions=["city"], unresolved_concepts=[ASKS_PERIOD]),
    )
    main = execute.return_value

    def result(columns, rows):
        return [SimpleNamespace(columns=columns, rows=rows, row_count=len(rows), error=None)]

    execute = AsyncMock(side_effect=[
        main,
        result(["order_date", "total_revenue"], [["2025-01-03", 1]]),
        result(["order_date", "total_revenue"], [["2026-10-31", 1]]),
    ])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    outcome = await _run(tool, "Revenue per city")
    assert outcome.ok, outcome.safe_detail
    assert "The data covers 2025-01-03 to 2026-10-31" in outcome.summary
    assert "(3 January 2025 to 31 October 2026)" in outcome.summary
    assert outcome.table["period"] == {"first": "2025-01-03", "last": "2026-10-31",
                                       "scope": "data"}
    assert "Not shown by this query" not in outcome.summary
    assert not outcome.data["semantic_plan"]["unresolved_concepts"]
    assert execute.await_count == 3


async def test_dates_that_cannot_be_read_are_said_plainly(monkeypatch):
    tool, _provider, _execute = setup_tool(
        monkeypatch, _plan(dimensions=["city"], unresolved_concepts=[ASKS_PERIOD]),
    )
    outcome = await _run(tool, "Revenue per city")
    assert outcome.ok, outcome.safe_detail
    assert "The dates the data covers could not be read" in outcome.summary


def test_a_plan_with_only_unresolved_presentation_requests_is_not_a_plan():
    with pytest.raises(SemanticPlanError):
        SemanticPlan.from_dict(_plan(metrics=[], unresolved_concepts=[
            {"text": "the period covered", "type_hint": "presentation", "material": False},
        ]))


async def test_the_result_carries_the_units_the_view_declares(monkeypatch):
    model = sales_model()
    money = replace(model.metrics[0], unit="IDR")
    tool, _provider, _execute = setup_tool(monkeypatch, _plan(dimensions=["city"]))
    use_model(monkeypatch, tool, replace(model, metrics=(money,)))
    outcome = await _run(tool, "Revenue per city")
    assert outcome.ok, outcome.safe_detail
    assert outcome.table["units"] == {"total_revenue": "IDR"}
    assert "period" not in outcome.table
