"""Configured calendars produce the same concrete SQL bounds and evidence."""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.planning import SemanticPlan, SemanticTime
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_semantic_guidance_fallback import USER
from tests.unit.test_semantic_intelligence import sales_model
from tests.unit.test_semantic_llm_planner import _plan, setup_tool

ZONES = ("Asia/Jakarta", "UTC", "Europe/Berlin", "America/New_York")
NOW = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
BOUNDARIES = {
    "Asia/Jakarta": {
        "today": "2025-12-31T17:00:00+00:00",
        "this_week": "2025-12-28T17:00:00+00:00",
        "this_month": "2025-12-31T17:00:00+00:00",
        "previous_month": "2025-11-30T17:00:00+00:00",
    },
    "UTC": {
        "today": "2026-01-01T00:00:00+00:00",
        "this_week": "2025-12-29T00:00:00+00:00",
        "this_month": "2026-01-01T00:00:00+00:00",
        "previous_month": "2025-12-01T00:00:00+00:00",
    },
    "Europe/Berlin": {
        "today": "2025-12-31T23:00:00+00:00",
        "this_week": "2025-12-28T23:00:00+00:00",
        "this_month": "2025-12-31T23:00:00+00:00",
        "previous_month": "2025-11-30T23:00:00+00:00",
    },
    "America/New_York": {
        "today": "2025-12-31T05:00:00+00:00",
        "this_week": "2025-12-29T05:00:00+00:00",
        "this_month": "2025-12-01T05:00:00+00:00",
        "previous_month": "2025-11-01T04:00:00+00:00",
    },
}


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("period", ("today", "this_week", "this_month", "previous_month"))
async def test_configured_tool_calendar_crosses_year_boundary(monkeypatch, zone, period):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", f" {zone} ")
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    tool, _, execute = setup_tool(monkeypatch, _plan(time={
        "dimension": "order_date", "grain": None, "range": period, "compare": None,
    }))
    tool._resolve_model.return_value.update(status="ACTIVE", version=1, fingerprint="published")
    context = SimpleNamespace(user=USER.copy(), execution_now=NOW)
    outcome = await tool.run(
        ToolInvocation("calendar", "semantic_query", {"question": "Revenue in this period"}),
        context,
    )
    assert outcome.ok, outcome.safe_detail
    fixed = outcome.trace_detail["execution_time_context"]
    assert fixed["timezone"] == zone and fixed["now"] == NOW.isoformat()
    assert fixed["current"]["start"] == BOUNDARIES[zone][period]
    expected_end = BOUNDARIES[zone]["this_month"] if period == "previous_month" else NOW.isoformat()
    assert fixed["current"]["end"] == expected_end
    sql = execute.await_args.kwargs["sql"]
    assert "CURRENT_DATE" not in sql
    for instant in fixed["current"].values():
        local = datetime.fromisoformat(instant).astimezone(ZoneInfo(zone))
        assert local.strftime("%Y-%m-%d %H:%M:%S.%f") in sql
    assert execute.await_args.kwargs["username"] == USER["username"]


@pytest.mark.parametrize("override", ("America/New_York", ""))
async def test_explicit_tool_timezone_is_preserved_or_rejected(monkeypatch, override):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    monkeypatch.setattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", True)
    tool, _, execute = setup_tool(monkeypatch, _plan(time={
        "dimension": "order_date", "grain": None, "range": "today", "compare": None,
    }))
    context = LoopContext(user_name="alice", user=USER.copy(), execution_now=NOW,
                          execution_timezone=override)
    outcome = await tool.run(ToolInvocation("override", "semantic_query", {
        "question": "Revenue today",
    }), context)
    if not override:
        assert not outcome.ok and outcome.error_class == "INVALID_SEMANTIC_PLAN"
        execute.assert_not_awaited()
    else:
        assert outcome.ok
        fixed = outcome.trace_detail["execution_time_context"]
        assert fixed["timezone"] == override
        assert fixed["current"]["start"] == BOUNDARIES[override]["today"]


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("period,comparison,start,baseline_start,baseline_end", [
    ("this_month", "month_over_month", "2026-05-01", "2026-04-01", "2026-04-15T10:30:00"),
    ("current_quarter", "quarter_over_quarter", "2026-04-01", "2026-01-01",
     "2026-02-14T10:30:00"),
    ("current_year", "year_over_year", "2026-01-01", "2025-01-01", "2025-05-15T10:30:00"),
])
def test_comparisons_use_equal_elapsed_local_calendar_spans(
    zone, period, comparison, start, baseline_start, baseline_end,
):
    calendar = ZoneInfo(zone)
    now = datetime(2026, 5, 15, 10, 30, tzinfo=calendar)
    fixed = resolve_execution_time(period, comparison, now=now, timezone=zone)
    assert fixed.current.start == datetime.fromisoformat(start).replace(tzinfo=calendar)
    assert fixed.current.end == now
    assert fixed.baseline.start == datetime.fromisoformat(baseline_start).replace(tzinfo=calendar)
    assert fixed.baseline.end == datetime.fromisoformat(baseline_end).replace(tzinfo=calendar)
    plan = SemanticPlan(metrics=("total_revenue",), time=SemanticTime(
        "order_date", range=period, compare=comparison,
    ))
    sql = SemanticCompiler().compile(sales_model(), plan, time_context=fixed).sql
    assert all(bound in sql for window in (fixed.current, fixed.baseline)
               for bound in window.sql_bounds())
    assert "CURRENT_DATE" not in sql


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("year,current_end", [(2026, "2026-03-29"), (2024, "2024-03-30")])
def test_month_end_comparisons_clamp_to_complete_february(zone, year, current_end):
    calendar = ZoneInfo(zone)
    fixed = resolve_execution_time(
        "current_month", "month_over_month", timezone=zone,
        now=datetime(year, 3, 31, 10, 30, tzinfo=calendar),
    )
    assert fixed.current.start == datetime(year, 3, 1, tzinfo=calendar)
    assert fixed.current.end == datetime.fromisoformat(current_end).replace(tzinfo=calendar)
    assert fixed.baseline.start == datetime(year, 2, 1, tzinfo=calendar)
    assert fixed.baseline.end == datetime(year, 3, 1, tzinfo=calendar)
    assert "calendar_span_clamped" in fixed.warnings


@pytest.mark.parametrize("zone", ZONES)
def test_leap_year_comparison_keeps_prior_year_calendar_limit(zone):
    calendar = ZoneInfo(zone)
    fixed = resolve_execution_time(
        "current_year", "year_over_year", timezone=zone,
        now=datetime(2024, 12, 31, 12, tzinfo=calendar),
    )
    assert fixed.current.start == datetime(2024, 1, 1, tzinfo=calendar)
    assert fixed.current.end == datetime(2024, 12, 31, tzinfo=calendar)
    assert fixed.baseline.start == datetime(2023, 1, 1, tzinfo=calendar)
    assert fixed.baseline.end == datetime(2024, 1, 1, tzinfo=calendar)
    assert "calendar_span_clamped" in fixed.warnings


@pytest.mark.parametrize("zone,now,start,end,hours", [
    ("Europe/Berlin", "2026-03-30T12:00:00+02:00", "2026-03-28T23:00:00+00:00",
     "2026-03-29T22:00:00+00:00", 23),
    ("Europe/Berlin", "2026-10-26T12:00:00+01:00", "2026-10-24T22:00:00+00:00",
     "2026-10-25T23:00:00+00:00", 25),
    ("America/New_York", "2026-03-09T12:00:00-04:00", "2026-03-08T05:00:00+00:00",
     "2026-03-09T04:00:00+00:00", 23),
    ("America/New_York", "2026-11-02T12:00:00-05:00", "2026-11-01T04:00:00+00:00",
     "2026-11-02T05:00:00+00:00", 25),
])
def test_dst_days_keep_calendar_boundaries_and_report_duration_difference(
    zone, now, start, end, hours,
):
    fixed = resolve_execution_time(
        "previous_day", "previous_period", now=datetime.fromisoformat(now), timezone=zone,
    )
    proof = fixed.as_dict()
    assert proof["current"] == {"start": start, "end": end}
    assert proof["current_duration_seconds"] == hours * 3600
    assert proof["baseline_duration_seconds"] == 24 * 3600
    assert proof["warnings"] == ["dst_duration_difference"]
