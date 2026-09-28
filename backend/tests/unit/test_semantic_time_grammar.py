"""Time-range grammar: plan strings, compiled bounds, and question phrases.

The regression cases at the bottom reproduce the 2026-09-27 Studio audit:
"last quarter" compiled as an all-time quarterly grouping, "tahun lalu" failed
as a comparison without a period, and Q2 / month ranges / "last 7 days" / YTD
were reported as unresolved business concepts.
"""

from __future__ import annotations

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.plan_contract import validate_generated_plan
from app.modules.agents.semantic.planning import (
    SemanticPlan,
    SemanticPlanError,
    SemanticPlanner,
    SemanticTime,
)
from app.modules.agents.semantic.time_ranges import (
    comparison_bounds,
    parse_time_phrase,
    range_predicates,
    resolve_time_range,
)
from tests.unit.test_semantic_intelligence import sales_model

TRUNC_MONTH = "DATE_TRUNC('month', CURRENT_DATE())"
TRUNC_QUARTER = "DATE_TRUNC('quarter', CURRENT_DATE())"
TRUNC_YEAR = "DATE_TRUNC('year', CURRENT_DATE())"
TOMORROW = "DATE_ADD(CURRENT_DATE(), INTERVAL 1 DAY)"


# ── plan range strings → SQL bounds ─────────────────────────────────────────

@pytest.mark.parametrize(
    ("value", "start", "end"),
    [
        # Forms that existed before the grammar keep byte-identical SQL, so
        # stored verified queries still match.
        ("current_month", TRUNC_MONTH, f"DATE_ADD({TRUNC_MONTH}, INTERVAL 1 MONTH)"),
        ("previous_month", f"DATE_SUB({TRUNC_MONTH}, INTERVAL 1 MONTH)", TRUNC_MONTH),
        ("current_quarter", TRUNC_QUARTER, f"DATE_ADD({TRUNC_QUARTER}, INTERVAL 3 MONTH)"),
        (
            "current_week",
            "DATE_TRUNC('week', CURRENT_DATE())",
            "DATE_ADD(DATE_TRUNC('week', CURRENT_DATE()), INTERVAL 1 WEEK)",
        ),
        ("last_30_days", "DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)", "CURRENT_DATE()"),
        ("last_3_months", f"DATE_SUB({TRUNC_MONTH}, INTERVAL 3 MONTH)", TRUNC_MONTH),
        ("last 3 months", f"DATE_SUB({TRUNC_MONTH}, INTERVAL 3 MONTH)", TRUNC_MONTH),
        ("2025", "'2025-01-01'", "'2026-01-01'"),
        ("since_2023", "'2023-01-01'", TOMORROW),
        ("2023-01-01+", "'2023-01-01'", TOMORROW),
        # New forms.
        ("previous_quarter", f"DATE_SUB({TRUNC_QUARTER}, INTERVAL 3 MONTH)", TRUNC_QUARTER),
        ("last_quarter", f"DATE_SUB({TRUNC_QUARTER}, INTERVAL 3 MONTH)", TRUNC_QUARTER),
        ("previous_year", f"DATE_SUB({TRUNC_YEAR}, INTERVAL 1 YEAR)", TRUNC_YEAR),
        ("current_year", TRUNC_YEAR, f"DATE_ADD({TRUNC_YEAR}, INTERVAL 1 YEAR)"),
        ("today", "CURRENT_DATE()", TOMORROW),
        ("yesterday", "DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)", "CURRENT_DATE()"),
        ("last_7_days", "DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)", "CURRENT_DATE()"),
        ("last_2_quarters", f"DATE_SUB({TRUNC_QUARTER}, INTERVAL 6 MONTH)", TRUNC_QUARTER),
        ("ytd", TRUNC_YEAR, TOMORROW),
        ("year to date", TRUNC_YEAR, TOMORROW),
        ("qtd", TRUNC_QUARTER, TOMORROW),
        ("mtd", TRUNC_MONTH, TOMORROW),
        ("2025-Q2", "'2025-04-01'", "'2025-07-01'"),
        ("Q4-2025", "'2025-10-01'", "'2026-01-01'"),
        ("2025-03", "'2025-03-01'", "'2025-04-01'"),
        ("2025-12", "'2025-12-01'", "'2026-01-01'"),
        ("2025-01-01..2025-03-31", "'2025-01-01'", "'2025-04-01'"),
    ],
)
def test_range_resolves_to_half_open_bounds(value, start, end):
    window = resolve_time_range(value)
    assert (window.start, window.end) == (start, end)
    assert range_predicates("`d`", value) == [f"`d` >= {start}", f"`d` < {end}"]


@pytest.mark.parametrize(
    "value",
    [
        "", "last quarterly", "next_month", "2025-13", "2025-Q5", "2025-02-30..2025-03-01",
        "2025-03-31..2025-01-01", "last_0_days", "last_11_years", "sometime",
    ],
)
def test_unknown_or_invalid_range_is_an_error_not_ignored(value):
    with pytest.raises(SemanticPlanError):
        resolve_time_range(value)


# ── comparisons ─────────────────────────────────────────────────────────────

def test_a_period_in_progress_is_compared_like_for_like():
    # This month so far against the same days of last month, not a full month.
    start, end, prior_start, prior_end = comparison_bounds("current_month", "previous_period")
    assert (start, end) == (TRUNC_MONTH, TOMORROW)
    assert prior_start == f"DATE_SUB({TRUNC_MONTH}, INTERVAL 1 MONTH)"
    assert prior_end == f"DATE_SUB({TOMORROW}, INTERVAL 1 MONTH)"


def test_a_complete_period_uses_the_window_length():
    start, end, prior_start, prior_end = comparison_bounds("previous_month", "previous_period")
    assert (start, end) == (f"DATE_SUB({TRUNC_MONTH}, INTERVAL 1 MONTH)", TRUNC_MONTH)
    assert prior_start == f"DATE_SUB(DATE_SUB({TRUNC_MONTH}, INTERVAL 1 MONTH), INTERVAL 1 MONTH)"
    assert prior_end == f"DATE_SUB({TRUNC_MONTH}, INTERVAL 1 MONTH)"


def test_absolute_ranges_shift_as_literals():
    assert comparison_bounds("2025-Q2", "previous_period")[2:] == ("'2025-01-01'", "'2025-04-01'")
    assert comparison_bounds("2025-Q2", "year_over_year")[2:] == ("'2024-04-01'", "'2024-07-01'")
    assert comparison_bounds("2025-03", "month_over_month")[2:] == ("'2025-02-01'", "'2025-03-01'")
    assert comparison_bounds("2025-01-01..2025-01-10", None)[2:] == (
        "'2024-12-22'", "'2025-01-01'"
    )


def test_ytd_compares_with_the_same_span_last_year():
    _start, _end, prior_start, prior_end = comparison_bounds("ytd", "year_over_year")
    assert prior_start == f"DATE_SUB({TRUNC_YEAR}, INTERVAL 1 YEAR)"
    assert prior_end == f"DATE_SUB({TOMORROW}, INTERVAL 1 YEAR)"


@pytest.mark.parametrize(
    ("value", "comparison"),
    [
        (None, "year_over_year"),
        ("since_2023", "previous_period"),
        ("current_quarter", "month_over_month"),
        ("2025", "week_over_week"),
        ("current_month", "custom"),
        ("current_month", "fortnight_over_fortnight"),
    ],
)
def test_incompatible_comparison_is_rejected(value, comparison):
    with pytest.raises(SemanticPlanError):
        comparison_bounds(value, comparison)


# ── question phrases (EN + ID) ──────────────────────────────────────────────

@pytest.mark.parametrize(
    ("question", "range_value", "compare", "grain"),
    [
        ("revenue last quarter", "previous_quarter", None, None),
        ("penjualan kuartal lalu", "previous_quarter", None, None),
        ("omzet triwulan lalu", "previous_quarter", None, None),
        ("revenue tahun lalu", "previous_year", None, None),
        ("revenue last year", "previous_year", None, None),
        ("penjualan bulan kemarin", "previous_month", None, None),
        ("orders yesterday", "previous_day", None, None),
        ("order hari ini", "current_day", None, None),
        ("revenue this week", "current_week", None, None),
        ("revenue pekan lalu", "previous_week", None, None),
        ("revenue last 7 days", "last_7_days", None, None),
        ("omzet 30 hari terakhir", "last_30_days", None, None),
        ("revenue past 6 months", "last_6_months", None, None),
        ("revenue year to date", "ytd", None, None),
        ("revenue sejak awal tahun", "ytd", None, None),
        ("revenue in Q2 2025", "2025-Q2", None, None),
        ("revenue kuartal 3 2025", "2025-Q3", None, None),
        ("revenue 2025 Q4", "2025-Q4", None, None),
        ("omzet Maret 2025", "2025-03", None, None),
        ("revenue from January to March 2025", "2025-01-01..2025-03-31", None, None),
        ("penjualan Januari sampai Juni 2025", "2025-01-01..2025-06-30", None, None),
        ("revenue between 2025-01-01 and 2025-03-31", "2025-01-01..2025-03-31", None, None),
        ("revenue since 2023", "since_2023", None, None),
        ("revenue in 2025", "2025", None, None),
        ("revenue by month in 2025", "2025", None, "month"),
        ("revenue per bulan tahun ini", "current_year", None, "month"),
        ("monthly revenue this year", "current_year", None, "month"),
        ("revenue this month vs last year", "current_month", "year_over_year", None),
        ("revenue bulan ini dibanding tahun lalu", "current_month", "year_over_year", None),
        ("revenue this month vs last month", "current_month", "month_over_month", None),
        ("revenue growth this quarter", "current_quarter", "previous_period", None),
        ("revenue Q2 2025 vs previous", "2025-Q2", "previous_period", None),
        # Channel comparison is not a period comparison.
        ("revenue online vs offline this month", "current_month", None, None),
        # A comparison without its period is reported, not guessed.
        ("revenue year over year", None, "year_over_year", None),
    ],
)
def test_question_phrase_names_range_compare_and_grain(question, range_value, compare, grain):
    phrase = parse_time_phrase(question)
    assert (phrase.range, phrase.compare, phrase.grain) == (range_value, compare, grain)


# ── audit regressions through the planner and compiler ──────────────────────

@pytest.mark.parametrize(
    ("question", "range_value"),
    [
        ("Revenue last quarter", "previous_quarter"),
        ("Revenue last year", "previous_year"),
        ("Revenue tahun lalu", "previous_year"),
        ("Revenue in Q2 2025", "2025-Q2"),
        ("Revenue from January to March 2025", "2025-01-01..2025-03-31"),
        ("Revenue last 7 days", "last_7_days"),
        ("Revenue year to date", "ytd"),
    ],
)
def test_planner_binds_the_requested_period_and_compiles(question, range_value):
    planned = SemanticPlanner().plan(sales_model(), question)
    assert planned.plan is not None, planned.confidence
    assert planned.confidence.unresolved == ()
    assert planned.plan.time is not None
    assert planned.plan.time.range == range_value
    # The audit bug: a period phrase became a grouping grain with no range.
    assert planned.plan.time.grain is None
    assert planned.plan.dimensions == ()
    window = resolve_time_range(range_value)
    sql = SemanticCompiler().compile(sales_model(), planned.plan).sql
    assert f">= {window.start}" in sql
    assert f"< {window.end}" in sql


def test_comparison_without_period_is_unresolved_not_a_compile_error():
    planned = SemanticPlanner().plan(sales_model(), "Revenue year over year")
    assert "comparison period" in planned.confidence.unresolved


def test_compiler_builds_year_over_year_for_an_absolute_quarter():
    sql = SemanticCompiler().compile(
        sales_model(),
        SemanticPlan(
            metrics=("total_revenue",),
            time=SemanticTime("order_date", range="2025-Q2", compare="year_over_year"),
        ),
    ).sql
    assert "CASE WHEN `orders`.`order_date` >= '2025-04-01'" in sql
    assert "`orders`.`order_date` >= '2024-04-01'" in sql
    assert "`orders`.`order_date` < '2024-07-01'" in sql


def _plan_with_range(value):
    return {
        "metrics": ["total_revenue"], "dimensions": [], "filters": [], "named_filters": [],
        "time": {"dimension": "order_date", "grain": None, "range": value, "compare": None},
        "order_by": [], "limit": None, "unresolved_concepts": [],
    }


def test_generated_plan_contract_accepts_grammar_and_rejects_unknown_ranges():
    validate_generated_plan(_plan_with_range("previous_quarter"))
    validate_generated_plan(_plan_with_range("2025-01-01..2025-03-31"))
    with pytest.raises(SemanticPlanError, match="Unsupported time.range"):
        validate_generated_plan(_plan_with_range("last quarter-ish"))
