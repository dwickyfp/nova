"""Calendar boundaries and calculation closure over the full grouped result."""

from datetime import UTC, datetime

import pytest

from app.modules.agents.semantic.calculations import Calculation, total_calculations
from app.modules.agents.semantic.time_ranges import resolve_execution_time
from app.modules.smart_reports.schemas import FilterValue


def test_fiscal_year_and_quarter_share_a_pinned_clock():
    now = datetime(2026, 1, 20, 10, tzinfo=UTC)
    fiscal = resolve_execution_time(
        "ytd", "year_over_year", now=now, timezone="Asia/Jakarta", fiscal_start_month=4
    )
    assert fiscal.current.start.date().isoformat() == "2025-04-01"
    assert fiscal.baseline.start.date().isoformat() == "2024-04-01"
    assert fiscal.current.end.hour == 17
    quarter = resolve_execution_time(
        "previous_quarter", now=now, timezone="UTC", fiscal_start_month=2
    )
    assert quarter.current.start.date().isoformat() == "2025-08-01"
    assert quarter.current.end.date().isoformat() == "2025-11-01"
    week = resolve_execution_time("current_week", now=now, timezone="UTC", week_start=6)
    assert week.current.start.date().isoformat() == "2026-01-18"


def test_totals_drop_transitive_window_dependencies_but_preserve_ratio_of_sums():
    definitions = tuple(
        Calculation.model_validate(value).model_dump()
        for value in [
            {
                "name": "ratio",
                "expression": {
                    "op": "divide",
                    "args": [
                        {"op": "field", "field": "revenue"},
                        {"op": "field", "field": "cost"},
                    ],
                },
            },
            {
                "name": "running",
                "expression": {
                    "op": "running_total",
                    "args": [
                        {"op": "field", "field": "revenue"},
                    ],
                    "order_by": [{"field": "period"}],
                },
            },
            {
                "name": "running_ratio",
                "expression": {
                    "op": "divide",
                    "args": [
                        {"op": "field", "field": "running"},
                        {"op": "field", "field": "cost"},
                    ],
                },
            },
        ]
    )
    assert [c["name"] for c in total_calculations(definitions, ("revenue", "cost"))] == ["ratio"]


def test_numeric_range_uses_decimal_order_not_lexical_or_binary_float_order():
    valid = FilterValue(kind="range", minimum="9.0000000000000001", maximum="10")
    assert valid.minimum == "9.0000000000000001"
    with pytest.raises(ValueError):
        FilterValue(kind="range", minimum="9007199254740993.12", maximum="9007199254740993.11")
    with pytest.raises(ValueError):
        FilterValue(kind="range", minimum=True)
    with pytest.raises(ValueError):
        FilterValue(kind="date_range", minimum="not a date")


def test_timezone_dst_leap_and_partial_periods_use_calendar_bounds():
    spring = resolve_execution_time(
        "previous_day", now=datetime(2026, 3, 9, 16, tzinfo=UTC), timezone="America/New_York"
    )
    assert (
        spring.current.end.astimezone(UTC) - spring.current.start.astimezone(UTC)
    ).total_seconds() == 23 * 3600
    autumn = resolve_execution_time(
        "previous_day", now=datetime(2026, 11, 2, 16, tzinfo=UTC), timezone="America/New_York"
    )
    assert (
        autumn.current.end.astimezone(UTC) - autumn.current.start.astimezone(UTC)
    ).total_seconds() == 25 * 3600
    leap = resolve_execution_time(
        "2024-02", "year_over_year", now=datetime(2026, 1, 1, tzinfo=UTC), timezone="UTC"
    )
    assert (leap.current.end - leap.current.start).days == 28
    assert (leap.baseline.end - leap.baseline.start).days == 28
    assert "calendar_span_clamped" in leap.warnings
    complete = resolve_execution_time(
        "2024-02", now=datetime(2026, 1, 1, tzinfo=UTC), timezone="UTC"
    )
    assert (complete.current.end - complete.current.start).days == 29
    partial = resolve_execution_time(
        "mtd", "previous_period", now=datetime(2026, 3, 12, 10, tzinfo=UTC), timezone="UTC"
    )
    assert partial.current.end.isoformat() == "2026-03-12T10:00:00+00:00"
    assert partial.baseline.end.isoformat() == "2026-02-12T10:00:00+00:00"
