"""A year the data shows and a unit the view declares are read from the result.

The number check accepts a year the result's dates or queried period show and
still refuses one the data does not reach; a declared unit decides whether a
column is a percentage, so a money "margin" is never written with "%".
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.modules.agents.tools.semantic_query import _queried_period
from app.modules.assistant.answer_contract import (
    check_numeric_answer,
    is_percent_column,
    render_with_claims,
)
from app.modules.assistant.intelligence import EvidenceTracker

MONTHS = {"e1": {"columns": ["order_date", "net_revenue"],
                 "rows": [["2026-10-01", "1573810277.50"]]}}


@pytest.mark.parametrize("month", ["2026-10-01", date(2026, 10, 1), datetime(2026, 10, 1)])
def test_a_year_the_results_dates_show_is_accepted(month):
    # A driver returns a date column as dates; their years are data too.
    tables = {"e1": {"columns": ["order_date", "net_revenue"],
                     "rows": [[month, "1573810277.50"]]}}
    check = check_numeric_answer(
        "Oktober 2026 mencatat penjualan tertinggi, Rp1.573.810.277,50.",
        question="Bulan apa penjualan paling tinggi?", tables=tables, language="id",
    )
    assert check.accepted, check.unsupported


def test_a_year_the_data_does_not_reach_is_still_refused():
    check = check_numeric_answer(
        "Oktober 2025 mencatat penjualan tertinggi, Rp1.573.810.277,50.",
        question="Bulan apa penjualan paling tinggi?", tables=MONTHS, language="id",
    )
    assert check.unsupported == ("2025",)


def test_a_figure_that_looks_like_a_year_is_not_a_year():
    tables = {"e1": {"columns": ["units_sold"], "rows": [[17]]}}
    check = check_numeric_answer(
        "We sold 2.026 units.", question="Units sold?",
        tables={**tables, "e2": {"columns": ["day"], "rows": [["2026-01-01"]]}}, language="id",
    )
    assert check.unsupported == ("2.026",)


def test_the_years_of_the_period_the_data_covers_are_data():
    tables = {"e1": {"columns": ["gross_margin"], "rows": [["3524365749.03"]],
                     "period": {"first": "2026-05-01", "last": "2026-11-04"}}}
    check = check_numeric_answer(
        "Margin kotor Rp3.524.365.749,03 untuk Mei sampai November 2026.",
        question="Berapa margin kita?", tables=tables, language="id",
    )
    assert check.accepted, check.unsupported


def test_a_comparison_names_both_queried_years():
    current = SimpleNamespace(start=datetime(2026, 1, 1, tzinfo=UTC),
                              end=datetime(2027, 1, 1, tzinfo=UTC))
    baseline = SimpleNamespace(start=datetime(2025, 1, 1, tzinfo=UTC),
                               end=datetime(2026, 1, 1, tzinfo=UTC))
    period = _queried_period(SimpleNamespace(current=current, baseline=baseline))
    assert period == {"first": "2026-01-01", "last": "2026-12-31",
                      "compared_first": "2025-01-01", "compared_last": "2025-12-31"}
    assert _queried_period(None) is None
    tables = {"e1": {"columns": ["net_revenue"], "rows": [["9011367749.03"]],
                     "period": period}}
    check = check_numeric_answer(
        "Sales in 2026 were 9,011,367,749.03; year 2025 has no data.",
        question="Compare this year's sales with last year.", tables=tables, language="en",
    )
    assert check.accepted, check.unsupported


@pytest.mark.parametrize(("column", "units", "percent"), [
    ("gross_margin", {"gross_margin": "IDR"}, False),
    ("gross_margin", {}, True),
    ("return_rate", {"return_rate": "ratio"}, True),
    ("avg_discount_pct", {"avg_discount_pct": "percent"}, True),
    ("revenue", {"revenue": "percent"}, True),
])
def test_a_declared_unit_decides_whether_a_column_is_a_percentage(column, units, percent):
    assert is_percent_column(column, {"units": units}) is percent


def test_a_money_margin_is_rendered_without_a_percent_sign():
    tables = {"e1": {"columns": ["gross_margin"], "rows": [["3524365749.02779334"]],
                     "units": {"gross_margin": "IDR"}}}
    text, claims = render_with_claims(tables, language="id", include_table=False)
    assert "%" not in text
    assert "3.524.365.749,03" in text
    assert check_numeric_answer(
        text, question="Berapa margin kita?", tables=tables, claims=claims, language="id",
    ).accepted


def _stored(table):
    tracker = EvidenceTracker()
    tracker.add("semantic_query", "rows", table=table)
    return next(iter(tracker.business_tables.values()))


def test_evidence_keeps_declared_units_and_dates_but_nothing_else():
    stored = _stored({
        "columns": ["gross_margin"], "rows": [["1"]],
        "units": {"gross_margin": "IDR", "not_a_column": "IDR", "blank": " "},
        "period": {"first": "2026-05-01", "last": "late", "extra": "2026-01-01"},
    })
    assert stored["units"] == {"gross_margin": "IDR"}
    assert stored["period"] == {"first": "2026-05-01"}


@pytest.mark.parametrize(("scope", "kept"), [("data", {"scope": "data"}), ("everything", {})])
def test_evidence_keeps_only_the_whole_data_scope(scope, kept):
    stored = _stored({"columns": ["n"], "rows": [["1"]],
                      "period": {"first": "2026-05-01", "last": "2026-11-04", "scope": scope}})
    assert stored["period"] == {"first": "2026-05-01", "last": "2026-11-04", **kept}


def test_evidence_without_units_or_dates_keeps_the_old_shape():
    assert set(_stored({"columns": ["a"], "rows": [["1"]], "units": {}})) == {
        "columns", "rows", "truncated",
    }


RETURNS = {"e1": {"columns": ["product_name", "returned_units"],
                  "rows": [["Mie Instan Goreng 85g", "2745"]]}}


def test_a_number_inside_a_result_label_is_part_of_the_name():
    check = check_numeric_answer(
        "**Mie Instan Goreng 85g** was returned the most, with **2,745 units**.",
        question="Which product was returned the most?", tables=RETURNS, language="en",
    )
    assert check.accepted, check.unsupported


@pytest.mark.parametrize(("answer", "unsupported"), [
    # The right name with a wrong figure, and a name the result does not hold.
    ("Mie Instan Goreng 85g was returned 2,746 times.", ("2,746",)),
    ("Mie Instan Goreng 90g was returned 2,745 times.", ("90",)),
])
def test_a_label_does_not_hide_a_wrong_number(answer, unsupported):
    check = check_numeric_answer(
        answer, question="Which product was returned the most?", tables=RETURNS, language="en",
    )
    assert check.unsupported == unsupported


MARGIN = {"e1": {"columns": ["gross_margin", "net_revenue"],
                 "rows": [["3524365749.02779334", "9011367749.03"]],
                 "units": {"gross_margin": "IDR", "net_revenue": "IDR"}}}


def _margin_check(**claim):
    from app.modules.assistant.answer_contract import Claim

    return check_numeric_answer(
        "Margin kotor kita Rp3,52 miliar.", question="Berapa margin kita?",
        tables=MARGIN, claims=(Claim(text="3,52 miliar", kind="derived", **claim),),
        language="id",
    )


def test_a_rounded_cell_noted_as_derived_is_that_cell():
    assert _margin_check(column="gross_margin").accepted


@pytest.mark.parametrize("claim", [
    # Another column, a claimed change, or no column: not a rounded cell.
    {"column": "net_revenue"},
    {"column": "gross_margin", "direction": "up"},
    {},
])
def test_a_derived_claim_is_not_waved_through(claim):
    assert _margin_check(**claim).unsupported == ("3,52 miliar",)


def test_a_period_the_query_reported_without_rows_backs_its_year():
    from app.modules.assistant.answer_contract import with_period_facts

    tables = {"e1": {"columns": ["net_revenue"], "rows": [["9011367749.03"]]}}
    facts = [{"first": "2025-01-01", "last": "2025-12-31"}]
    checked = with_period_facts(tables, facts)
    answer = "This year's sales were 9,011,367,749.03; 2025 has no sales data."
    question = "Compare this year's sales with last year."
    assert check_numeric_answer(answer, question=question, tables=checked).accepted
    assert check_numeric_answer(answer, question=question, tables=tables).unsupported == (
        "2025",
    )
    # A period backs its years only: no figure, and an unrelated year still fails.
    wrong = "This year's sales were 9,011,367,749.03; 2023 has no sales data."
    assert check_numeric_answer(wrong, question=question, tables=checked).unsupported == (
        "2023",
    )
    assert with_period_facts(tables, []) is tables
