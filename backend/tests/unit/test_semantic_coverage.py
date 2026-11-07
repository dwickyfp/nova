"""A period without data is reported, not answered with an empty result."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.agents.semantic.coverage import (
    DataWindow,
    coverage_gap,
    data_window,
    future_period,
    looks_empty,
    period_without_data,
    window_plans,
)
from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_semantic_guidance_fallback import USER, setup_tool

WINDOW = DataWindow(first=date(2026, 5, 1), last=date(2026, 11, 4))


def plan(range_: str | None = "2026-12", compare: str | None = None) -> SemanticPlan:
    return SemanticPlan(
        metrics=("total_revenue",), dimensions=("city",),
        time=SemanticTime("order_date", range=range_, compare=compare),
        named_filters=("completed_order",),
    )


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def test_an_empty_result_or_an_empty_column_looks_empty():
    assert looks_empty(["city", "revenue"], [])
    assert looks_empty(["month", "revenue", "previous"], [["2026-05", 10, None]])
    assert not looks_empty(["city", "revenue"], [["Jakarta", 0]])


def test_window_plans_read_the_first_and_last_day_of_the_metric_with_its_governed_filters():
    first, last = window_plans(plan())
    assert first["metrics"] == ["total_revenue"] and first["dimensions"] == []
    assert first["named_filters"] == ["completed_order"]
    assert first["time"]["grain"] == "day" and first["limit"] == 1
    assert first["order_by"] == [{"field": "order_date", "direction": "asc"}]
    assert last["order_by"] == [{"field": "order_date", "direction": "desc"}]
    assert window_plans(SemanticPlan(metrics=("total_revenue",))) is None
    # The window plans pass the same validation every plan does.
    from app.modules.agents.semantic.planning import validate_plan
    from tests.unit.test_semantic_guidance_fallback import guided_model

    for item in (first, last):
        assert validate_plan(guided_model(), SemanticPlan.from_dict(item)) == []


def test_a_period_wholly_outside_the_window_is_a_gap_and_an_overlap_is_not():
    december = coverage_gap(WINDOW, (at(date(2026, 12, 1)), at(date(2027, 1, 1))))
    assert december is not None and december.period == "requested"
    assert (december.start, december.end) == (date(2026, 12, 1), date(2026, 12, 31))
    april_30 = coverage_gap(WINDOW, (at(date(2026, 4, 30)), at(date(2026, 5, 1))))
    assert april_30 is not None
    assert coverage_gap(WINDOW, (at(date(2026, 4, 1)), at(date(2026, 6, 1)))) is None
    assert coverage_gap(WINDOW, (at(date(2026, 11, 4)), at(date(2026, 11, 5)))) is None


def test_a_comparison_baseline_before_the_data_is_a_gap():
    gap = coverage_gap(
        WINDOW,
        (at(date(2026, 5, 1)), at(date(2026, 11, 5))),
        (at(date(2025, 5, 1)), at(date(2025, 11, 5))),
    )
    assert gap is not None and gap.period == "comparison"
    assert "comparison period 2025-05-01 to 2025-11-04 has no data" in gap.detail("net_revenue")
    assert "2026-05-01 to 2026-11-04" in gap.detail("net_revenue")


def test_a_period_that_has_not_started_has_no_data_yet():
    assert "after today (2026-11-08)" in future_period(plan("2026-12"), date(2026, 11, 8))
    assert future_period(plan("2026-11"), date(2026, 11, 8)) is None
    assert future_period(plan("previous_month"), date(2026, 11, 8)) is None
    assert future_period(plan(None), date(2026, 11, 8)) is None


async def test_the_window_is_read_only_when_the_result_is_empty():
    run = AsyncMock(side_effect=[
        (["order_date", "total_revenue"], [["2026-05-01", 4]], None),
        (["order_date", "total_revenue"], [[datetime(2026, 11, 4, 9), 7]], None),
    ])
    context = SimpleNamespace(
        current=SimpleNamespace(start=at(date(2026, 12, 1)), end=at(date(2027, 1, 1))),
        baseline=None,
    )
    assert await period_without_data(plan(), ["city"], [["Jakarta", 1]], context, run) is None
    run.assert_not_awaited()
    gap = await period_without_data(plan(), ["city"], [], context, run)
    assert gap is not None and gap.window == WINDOW
    assert await period_without_data(plan(), ["city"], [], None, run) is None


async def test_no_window_claim_without_dated_rows():
    failing = AsyncMock(return_value=([], [], "query error"))
    assert await data_window(plan(), failing) is None
    empty = AsyncMock(return_value=(["order_date"], [], None))
    assert await data_window(plan(), empty) is None


async def test_semantic_query_reports_a_period_outside_the_data(monkeypatch):
    from app.modules.query.service import query_service

    dated = {**plan("2026-04-30..2026-04-30").as_dict(), "dimensions": [], "named_filters": []}
    tool, _provider, _execute = setup_tool(monkeypatch, dated)
    execute = AsyncMock(side_effect=[
        [SimpleNamespace(columns=["total_revenue"], rows=[[None]], error=None)],
        [SimpleNamespace(columns=["order_date", "total_revenue"],
                         rows=[["2026-05-01", 3]], error=None)],
        [SimpleNamespace(columns=["order_date", "total_revenue"],
                         rows=[["2026-11-04", 5]], error=None)],
    ])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    context = LoopContext(user_name="alice", user=USER.copy())
    context.execution_now = datetime(2026, 11, 8, 3, tzinfo=UTC)
    outcome = await tool.run(
        ToolInvocation(tool_call_id="window", tool_name="semantic_query",
                       arguments={"question": "Revenue on 30 April 2026?"}),
        context,
    )
    assert not outcome.ok
    assert outcome.error_class == "OUTSIDE_DATA_WINDOW" and not outcome.recoverable
    assert outcome.repair_context["data_window"] == {"first": "2026-05-01", "last": "2026-11-04"}
    assert execute.await_count == 3
    # The window is read as the caller, never with another identity.
    assert {call.kwargs["username"] for call in execute.await_args_list} == {"alice"}
    assert all(call.kwargs["role"] == "analyst" for call in execute.await_args_list)


async def test_semantic_query_refuses_a_period_that_has_not_started(monkeypatch):
    future = {**plan("2026-12").as_dict(), "dimensions": [], "named_filters": []}
    tool, _provider, execute = setup_tool(monkeypatch, future)
    context = LoopContext(user_name="alice", user=USER.copy())
    context.execution_now = datetime(2026, 11, 8, 3, tzinfo=UTC)
    outcome = await tool.run(
        ToolInvocation(tool_call_id="future", tool_name="semantic_query",
                       arguments={"question": "Revenue in December 2026?"}),
        context,
    )
    assert outcome.error_class == "OUTSIDE_DATA_WINDOW"
    assert "has not started" in outcome.error
    execute.assert_not_awaited()
