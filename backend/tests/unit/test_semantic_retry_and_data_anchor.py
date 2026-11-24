"""A failed first plan is not retried as is, and recent periods count from the data.

Replays of recorded Studio turns (re-bench 2026-11-24): trend-003 and
multi_step-010 retried the turn planner's failed week-over-week plan until the
turn ended; date_relative-002 ("the last 7 days of the data there is") and
what_if-006 ("July", no year) were read against today's calendar, outside data
that ends on 2025-07-05.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.semantic.coverage import CoverageGap, DataWindow, anchor_to_data
from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
from app.modules.agents.tools.semantic_query import _plan_error_message
from app.modules.assistant.intent import IntentFrame
from tests.unit.test_semantic_llm_planner import _ask, _context, _plan, answer_queries, setup_tool

WEEKLY = {"dimension": "order_date", "grain": "week", "range": None, "compare": None}


def _data(first: str, last: str, answers: dict[str, list]):
    """The engine: the data's first and last day, and rows for the periods it holds."""

    async def execute(**kwargs):
        sql = kwargs["sql"]
        if "< '2100-01-01" in sql:
            day = first if sql.rstrip().upper().split("ORDER BY")[-1].find("ASC") >= 0 else last
            rows = [[day, 1]]
            columns = ["order_date", "total_revenue"]
        else:
            columns = ["total_revenue"]
            rows = next((value for key, value in answers.items() if key in sql), [])
        return [SimpleNamespace(columns=columns, rows=rows, row_count=len(rows), error=None)]

    return execute


@pytest.fixture(autouse=True)
def _utc(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.NOVA_TIMEZONE", "UTC")


async def test_a_failed_turn_plan_is_planned_again_from_the_rewrite(monkeypatch):
    # trend-003: the turn planner read "week over week" as a comparison with no
    # period; the compiler refuses that every time, so the retry must replan.
    tool, provider, execute = setup_tool(monkeypatch, _plan(time=WEEKLY))
    primary = {"view": None, "plan": _plan(time={
        "dimension": "order_date", "grain": "week", "range": None,
        "compare": "week_over_week",
    })}
    context = _context(IntentFrame(compare=None, grain="week", asks_series=True),
                       primary=primary)
    first = await _ask(tool, context, "Is order volume growing or shrinking week over week?")
    assert not first.ok and first.error_class == "INVALID_SEMANTIC_PLAN"
    assert context.primary_query_done
    provider.complete.assert_not_awaited()
    retry = await _ask(tool, context, "Return order_count grouped by calendar week, no filter.")
    assert retry.ok, retry.safe_detail
    provider.complete.assert_awaited_once()
    assert "DATE_TRUNC('week'" in answer_queries(execute)[-1].kwargs["sql"]


async def test_the_last_7_days_of_the_data_count_back_from_its_last_day(monkeypatch):
    # date_relative-002: "Pendapatan 7 hari terakhir dari data yang ada?"
    tool, _provider, execute = setup_tool(monkeypatch)
    execute.side_effect = _data("2024-08-01", "2025-07-05", {"2025-06-29": [[4200]]})
    week = {"dimension": "order_date", "grain": None, "range": "last_7_days", "compare": None}
    context = _context(IntentFrame(language="id", range="last_7_days", anchor="data"),
                       primary={"view": None, "plan": _plan(time=week)})
    outcome = await _ask(tool, context, "Pendapatan 7 hari terakhir dari data yang ada?")
    assert outcome.ok, outcome.safe_detail
    sql = answer_queries(execute)[-1].kwargs["sql"]
    assert ">= '2025-06-29 00:00:00.000000'" in sql
    assert "< '2025-07-06 00:00:00.000000'" in sql
    assert "counted back from that day" in outcome.summary


async def test_a_month_without_a_year_moves_to_the_one_year_the_data_holds(monkeypatch):
    # what_if-006: "target pendapatan Juli" read as July 2026.
    tool, _provider, execute = setup_tool(monkeypatch)
    execute.side_effect = _data("2024-08-01", "2025-07-05", {"2025-07-01": [[900]]})
    july = {"dimension": "order_date", "grain": None, "range": "2026-07", "compare": None}
    context = _context(IntentFrame(language="id", range="2026-07", anchor="data"),
                       primary={"view": None, "plan": _plan(time=july)})
    outcome = await _ask(tool, context, "Bikin simulasi: target pendapatan Juli")
    assert outcome.ok, outcome.safe_detail
    sql = answer_queries(execute)[-1].kwargs["sql"]
    assert ">= '2025-07-01" in sql and "< '2025-08-01" in sql
    assert "2025-07" in outcome.summary


@pytest.mark.parametrize(("frame", "first", "rng"), [
    # Two Julys in the data: which one is the user's to say.
    (IntentFrame(range="2026-07", anchor="data"), "2024-01-01", "2026-07"),
    # The user tied the period to today: the gap is the answer.
    (IntentFrame(range="last_7_days", anchor="today"), "2024-08-01", "last_7_days"),
    # A year the user named is never moved.
    (IntentFrame(range="2026-07"), "2024-08-01", "2026-07"),
])
async def test_a_period_the_user_pinned_stays_outside_the_data(monkeypatch, frame, first, rng):
    tool, _provider, execute = setup_tool(monkeypatch)
    execute.side_effect = _data(first, "2025-07-05", {"2025-0": [[900]]})
    time = {"dimension": "order_date", "grain": None, "range": rng, "compare": None}
    context = _context(frame, primary={"view": None, "plan": _plan(time=time)})
    outcome = await _ask(tool, context)
    assert not outcome.ok and outcome.error_class == "OUTSIDE_DATA_WINDOW"
    assert outcome.repair_context["data_window"] == {"first": first, "last": "2025-07-05"}
    # The same plan would land outside the data again: the retry replans.
    assert context.primary_query_done


def test_only_the_requested_period_is_anchored():
    plan = SemanticPlan(metrics=("total_revenue",), time=SemanticTime(
        "order_date", range="current_month", compare="month_over_month"))
    window = DataWindow(first=__import__("datetime").date(2025, 1, 1),
                        last=__import__("datetime").date(2025, 7, 5))
    baseline = CoverageGap(window=window, period="comparison",
                           start=window.first, end=window.first)
    assert anchor_to_data(plan, baseline, None) is None
    requested = CoverageGap(window=window, period="requested", start=window.last,
                            end=window.last)
    anchored = anchor_to_data(plan, requested, None)
    assert anchored is not None and anchored.clock_day.isoformat() == "2025-07-06"


def test_plan_failures_get_messages_that_match_their_cause():
    assert "has no days that have passed" in _plan_error_message(
        "The execution window has no elapsed observations.")
    assert "needs a period to compare" in _plan_error_message(
        "Comparison needs an explicit time range.")
    assert "could not compare those periods" in _plan_error_message(
        "Comparison windows overlap; use nonoverlapping periods.")
