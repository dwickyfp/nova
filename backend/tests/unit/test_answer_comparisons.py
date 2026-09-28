"""Regressions found by the live benchmark (L3, 2026-09-28).

* Dates in prose ("2026-07-01") were split into unsupported numbers, so a
  correct growth answer was replaced.
* "Website lebih tinggi dari Marketplace" passed although Marketplace was higher:
  only numbers were checked, not the direction of a comparison.
"""

from __future__ import annotations

import pytest

from app.modules.assistant.answer_contract import (
    check_comparisons,
    check_numeric_answer,
    finalize_verified_answer,
)

MONTHS = {"e1": {"columns": ["order_date", "total_revenue"],
                 "rows": [["2026-07-01", "626798000.00"], ["2026-08-01", "588463000.00"]]}}
CHANNELS = {"e1": {"columns": ["sales_channel", "total_revenue"],
                   "rows": [["Website", "140847000.00"], ["Marketplace", "146353000.00"]]}}
CITIES = {"e1": {"columns": ["city", "total_revenue"],
                 "rows": [["Jakarta", 900], ["Medan", 1200], ["Bandung", 400]]}}


def test_dates_in_prose_are_not_numeric_claims():
    assert check_numeric_answer(
        "Revenue 2026-08-01 turun 6,12% dibanding 2026-07-01.",
        question="Pertumbuhan bulan lalu?", tables=MONTHS,
    ).accepted


def test_wrong_direction_of_a_change_is_still_rejected():
    assert not check_numeric_answer(
        "Revenue 2026-08-01 naik 6,12% dibanding 2026-07-01.",
        question="Pertumbuhan bulan lalu?", tables=MONTHS,
    ).accepted


@pytest.mark.parametrize(
    ("answer", "wrong"),
    [
        ("Website lebih tinggi dari Marketplace.", True),
        ("Marketplace lebih tinggi dari Website.", False),
        ("Website lebih rendah daripada Marketplace.", False),
        ("Website sales were higher than Marketplace.", True),
    ],
)
def test_pairwise_comparisons_are_checked_against_cells(answer, wrong):
    assert bool(check_comparisons(answer, CHANNELS)) is wrong


@pytest.mark.parametrize(
    ("answer", "wrong"),
    [
        ("Jakarta adalah kota tertinggi.", False),
        ("Jakarta tertinggi bulan ini.", True),
        ("Medan is the highest.", False),
        ("Medan terendah.", True),
        ("Bandung terendah.", False),
    ],
)
def test_leader_claims_are_checked_against_cells(answer, wrong):
    # "Jakarta adalah kota tertinggi" has a word between the label and the rank,
    # so it is not read as a claim; the checker prefers missing a claim to
    # flagging a correct sentence.
    assert bool(check_comparisons(answer, CITIES)) is wrong


def test_a_wrong_comparison_is_replaced_and_corrected_in_the_user_language():
    result = finalize_verified_answer(
        "Selisihnya 5.506.000 (Website lebih tinggi dari Marketplace).",
        question="Selisih penjualan Website dan Marketplace?", tables={
            **CHANNELS,
            "e2": {"columns": ["from", "to", "total_revenue_difference"],
                   "rows": [["Website", "Marketplace", "5506000.0000"]]},
        },
    )
    assert "Website lebih tinggi" not in result.text
    assert "[perbandingan dikoreksi di bawah]" in result.text
    assert "Marketplace (146.353.000) > Website (140.847.000)" in result.text
    assert result.check.accepted


def test_down_and_query_window_counts_are_understood():
    tables = {
        **MONTHS,
        "e2": {"columns": ["from", "to", "total_revenue_pct_change"],
               "rows": [["2026-07-01", "2026-08-01", "-6.1160"]]},
    }
    assert check_numeric_answer(
        "Over the last 3 months, 2026-08-01 vs 2026-07-01: total_revenue down 6.12%.",
        question="Revenue this month vs last month", tables=tables,
    ).accepted


@pytest.mark.parametrize(
    ("answer", "accepted"),
    [
        ("Medan peringkat 1 dengan 1.200.", True),
        ("1. Medan: 1.200\n2. Jakarta: 900", True),
        ("Medan is #1 at 1,200.", True),
        ("Medan is 3 times bigger.", False),
    ],
)
def test_positions_are_not_values_but_invented_multiples_are(answer, accepted):
    assert check_numeric_answer(answer, question="Kota paling laku?", tables=CITIES).accepted \
        is accepted


@pytest.mark.parametrize(
    ("answer", "wrong"),
    [
        ("Selisih (Website − Marketplace): 5.506.000", True),
        ("Website dikurangi Marketplace sebesar 5.506.000", True),
        ("Website dikurangi Marketplace sebesar -5.506.000", False),
        ("Marketplace - Website = 5.506.000", False),
    ],
)
def test_subtraction_claims_keep_their_sign(answer, wrong):
    assert bool(check_comparisons(answer, CHANNELS)) is wrong


def test_a_replacement_does_not_repeat_a_table_already_on_screen():
    result = finalize_verified_answer(
        "Medan 5, Jakarta 6, Bandung 7.", question="Penjualan per kota?", tables=CITIES,
        tables_shown=True,
    )
    assert result.replaced
    assert "| city |" not in result.text
    assert result.text.startswith("Angka berikut diambil langsung dari hasil query.")


def test_mostly_verified_prose_is_kept_with_only_bad_numbers_removed():
    result = finalize_verified_answer(
        "Medan 1.200, Jakarta 900, Bandung 400, total 9.999.",
        question="Penjualan per kota?", tables=CITIES,
    )
    assert not result.replaced
    assert "Medan 1.200" in result.text and "9.999" not in result.text


SCALAR = {"e1": {"columns": ["order_count"], "rows": [[878]]}}


def test_a_wrong_headline_is_rebuilt_as_a_sentence_not_reported_as_no_result():
    result = finalize_verified_answer(
        "Jumlah pesanan 3 bulan terakhir 1.424.",
        question="Berapa jumlah pesanan 3 bulan terakhir?", tables=SCALAR, tables_shown=True,
    )
    assert result.replaced
    assert "Tidak ada hasil" not in result.text
    assert "order count: 878." in result.text


def test_calendar_days_next_to_month_names_are_dates():
    assert check_numeric_answer(
        "Order count 878 dari 1 Januari sampai 31 Maret 2025; March 31 inclusive.",
        question="Berapa jumlah pesanan Januari sampai Maret 2025?", tables=SCALAR,
    ).accepted


SHARES = {
    "e1": {
        "columns": ["sales_channel", "total_revenue", "total_revenue_share_pct"],
        "rows": [
            ["Store", "148867000", "25.2976"], ["Website", "140847000", "23.9347"],
            ["Mobile App", "152396000", "25.8973"],
        ],
    },
}


def test_a_list_marker_after_a_percent_line_is_a_position():
    assert check_numeric_answer(
        "1. **Mobile App**: 152.396.000 (25,90% dari total)\n2. **Store**: 148.867.000",
        question="Top 2 kanal", tables=SHARES,
    ).accepted


def test_percent_written_as_a_word_and_ranges_are_percentages():
    assert check_numeric_answer(
        "Porsinya merata di kisaran 24 sampai 26 persen.", question="Porsi kanal?",
        tables=SHARES,
    ).accepted
    assert check_numeric_answer(
        "Shares sit between 24 and 26 percent.", question="Channel share?", tables=SHARES,
    ).accepted
    assert not check_numeric_answer(
        "Porsinya merata di kisaran 24 sampai 31 persen.", question="Porsi kanal?",
        tables=SHARES,
    ).accepted


TOP_PER_CITY = {
    "e1": {
        "columns": ["category", "city", "product_revenue"],
        "rows": [
            ["Fashion", "Medan", "104898000"], ["Grocery", "Medan", "88355000"],
            ["Grocery", "Jakarta", "88210000"], ["Home", "Jakarta", "84689000"],
            ["Grocery", "Bandung", "81491000"], ["Fashion", "Bandung", "77326000"],
        ],
    },
}


def test_counts_of_the_result_are_not_invented_numbers():
    assert check_numeric_answer(
        "Ada 3 kota dengan 2 kategori teratas masing-masing, total 6 baris.",
        question="Top 2 categories per city", tables=TOP_PER_CITY,
    ).accepted
    assert not check_numeric_answer(
        "Ada 7 kota dalam hasil ini.", question="Top 2 categories per city",
        tables=TOP_PER_CITY,
    ).accepted
