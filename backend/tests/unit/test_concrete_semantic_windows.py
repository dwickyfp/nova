"""SQL and provenance share elapsed local calendar periods, including DST."""

from datetime import UTC, datetime

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError, SemanticTime
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.assistant.planning import _output_instructions, _plan_schema
from tests.unit.test_semantic_intelligence import sales_model


def test_planner_field_instructions_follow_actual_schema():
    schema = _plan_schema(None)
    instructions = _output_instructions(schema)
    assert "primary_plan" not in instructions and "primary_view" not in instructions
    assert all(name in instructions for name in schema["properties"])
    schema = _plan_schema({"plan_schema": {"type": "object"}})
    assert all(name in _output_instructions(schema) for name in schema["properties"])
    assert "five keys" not in instructions
    restricted = _output_instructions({"properties": {
        "ml_task": {"enum": [None, "forecast"]},
    }})
    assert "forecast" in restricted and "clustering" not in restricted
    assert "intent_frame" not in restricted and "work_intent" not in restricted


def test_elapsed_month_clamps_both_periods_to_shorter_february():
    fixed = resolve_execution_time(
        "current_month", "previous_period", now=datetime(2026, 3, 31, 11, tzinfo=UTC),
        timezone="UTC",
    )
    assert fixed.current.start == datetime(2026, 3, 1, tzinfo=UTC)
    assert fixed.current.end == datetime(2026, 3, 29, tzinfo=UTC)
    assert fixed.baseline.start == datetime(2026, 2, 1, tzinfo=UTC)
    assert fixed.baseline.end == datetime(2026, 3, 1, tzinfo=UTC)
    assert "calendar_span_clamped" in fixed.warnings


def test_leap_year_span_and_dst_are_explicit():
    leap = resolve_execution_time(
        "current_year", "year_over_year", now=datetime(2024, 12, 31, 12, tzinfo=UTC),
        timezone="UTC",
    )
    assert leap.current.end == datetime(2024, 12, 31, tzinfo=UTC)
    assert "calendar_span_clamped" in leap.warnings
    dst = resolve_execution_time(
        "current_month", "previous_period", now=datetime(2026, 3, 20, 12, tzinfo=UTC),
        timezone="America/New_York",
    )
    assert "dst_duration_difference" in dst.warnings
    context = dst.as_dict()
    assert context["current_duration_seconds"] != context["baseline_duration_seconds"]


def test_concrete_compiler_uses_identical_half_open_bounds():
    fixed = resolve_execution_time(
        "current_month", "previous_period", now=datetime(2026, 10, 4, 12, tzinfo=UTC),
        timezone="Asia/Jakarta",
    )
    plan = SemanticPlan(metrics=("total_revenue",), time=SemanticTime(
        "order_date", range="current_month", compare="previous_period",
    ))
    compiled = SemanticCompiler().compile(sales_model(), plan, time_context=fixed)
    assert "CURRENT_DATE" not in compiled.sql
    assert all(bound in compiled.sql for window in (fixed.current, fixed.baseline)
               for bound in window.sql_bounds())
    assert fixed.current.end.hour == 19 and fixed.baseline.end.hour == 19


@pytest.mark.parametrize("value", ["current_day", "mtd", "qtd", "ytd", "since_2026",
                                  "2026-10-01+", "last_7_days", "previous_month"])
def test_range_grammar_uses_injected_clock(value):
    now = datetime(2026, 10, 4, 12, tzinfo=UTC)
    fixed = resolve_execution_time(value, now=now, timezone="Asia/Jakarta")
    assert fixed.current.start < fixed.current.end <= now


def test_clock_and_context_mismatch_fail_closed():
    with pytest.raises(SemanticPlanError):
        resolve_execution_time("current_month", now=datetime(2026, 10, 4), timezone="UTC")
    fixed = resolve_execution_time("current_month", now=datetime(2026, 10, 4, tzinfo=UTC),
                                   timezone="UTC")
    with pytest.raises(SemanticPlanError):
        SemanticCompiler().compile(sales_model(), SemanticPlan(metrics=("total_revenue",)),
                                   time_context=fixed)
