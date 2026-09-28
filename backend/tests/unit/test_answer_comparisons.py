"""Regressions found by the live benchmark (L3, 2026-09-28), checked by claims.

* Dates in prose ("2026-07-01") were split into unsupported numbers.
* "Website lebih tinggi dari Marketplace" passed although Marketplace was higher.

The check no longer reads words in any language: a comparison or a direction is
checked when the model claims it, and numbers are checked by value.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.assistant.answer_contract import (
    Claim,
    check_comparisons,
    check_numeric_answer,
    finalize_verified_answer,
    split_claims,
)

MONTHS = {"e1": {"columns": ["order_date", "total_revenue"],
                 "rows": [["2026-07-01", "626798000.00"], ["2026-08-01", "588463000.00"]]}}
CHANNELS = {"e1": {"columns": ["sales_channel", "total_revenue"],
                   "rows": [["Website", "140847000.00"], ["Marketplace", "146353000.00"]]}}
CITIES = {"e1": {"columns": ["city", "total_revenue"],
                 "rows": [["Jakarta", 900], ["Medan", 1200], ["Bandung", 400]]}}


def change(text, value, direction):
    return Claim(text=text, value=Decimal(value), kind="derived", direction=direction)


def comparison(text, relation, *labels):
    return Claim(text=text, kind="comparison", relation=relation, labels=labels)


def test_dates_in_prose_are_not_numeric_claims():
    assert check_numeric_answer(
        "Revenue 2026-08-01 turun 6,12% dibanding 2026-07-01.",
        question="Pertumbuhan bulan lalu?", tables=MONTHS, language="id",
    ).accepted


@pytest.mark.parametrize(("direction", "accepted"), [("down", True), ("up", False)])
def test_a_claimed_direction_is_checked_against_the_change(direction, accepted):
    assert check_numeric_answer(
        "Revenue 2026-08-01 berubah 6,12% dibanding 2026-07-01.",
        question="Pertumbuhan bulan lalu?", tables=MONTHS, language="id",
        claims=(change("6,12%", "6.12", direction),),
    ).accepted is accepted


PAIRS = [
    ("Website lebih tinggi dari Marketplace", "greater", ("Website", "Marketplace"), True),
    ("Marketplace lebih tinggi dari Website", "greater", ("Marketplace", "Website"), False),
    ("Website lebih rendah daripada Marketplace", "less", ("Website", "Marketplace"), False),
    ("ウェブサイトはマーケットプレイスより高い", "greater", ("Website", "Marketplace"), True),
]


@pytest.mark.parametrize(
    "claim, wrong",
    [(comparison(text, relation, *labels), wrong) for text, relation, labels, wrong in PAIRS],
)
def test_pairwise_comparisons_are_checked_against_cells(claim, wrong):
    assert bool(check_comparisons(claim.text, CHANNELS, (claim,))) is wrong


@pytest.mark.parametrize(("claim", "wrong"), [
    (comparison("Jakarta tertinggi", "max", "Jakarta"), True),
    (comparison("Medan is the highest", "max", "Medan"), False),
    (comparison("Medan terendah", "min", "Medan"), True),
    (comparison("Bandung es la más baja", "min", "Bandung"), False),
])
def test_leader_claims_are_checked_against_cells(claim, wrong):
    assert bool(check_comparisons(claim.text, CITIES, (claim,))) is wrong


def test_a_wrong_comparison_is_replaced_and_corrected_in_the_user_language():
    answer = "Selisihnya 5.506.000 (Website lebih tinggi dari Marketplace)."
    claims = (
        Claim(text="5.506.000", value=Decimal(5506000), kind="derived"),
        comparison("Website lebih tinggi dari Marketplace", "greater", "Website", "Marketplace"),
    )
    result = finalize_verified_answer(
        answer, question="Selisih penjualan Website dan Marketplace?", tables=CHANNELS,
        claims=claims, language="id",
    )
    assert "Website lebih tinggi" not in result.text
    assert "[perbandingan dikoreksi di bawah]" in result.text
    assert "Marketplace (146.353.000) > Website (140.847.000)" in result.text
    assert result.check.accepted


@pytest.mark.parametrize(("answer", "accepted"), [
    ("1. Medan: 1.200\n2. Jakarta: 900", True),
    ("Medan is 5 times bigger.", False),
    ("Medan is 3 times Bandung.", True),  # 1200 / 400, arithmetic over cells
])
def test_positions_and_multiples(answer, accepted):
    assert check_numeric_answer(
        answer, question="Kota paling laku?", tables=CITIES, language="id"
    ).accepted is accepted


@pytest.mark.parametrize(("direction", "accepted"), [("down", True), ("up", False)])
def test_a_difference_keeps_its_direction(direction, accepted):
    claims = (Claim(text="5.506.000", value=Decimal(5506000), kind="derived",
                    direction=direction, labels=("Website", "Marketplace")),)
    assert check_numeric_answer(
        "Website dibanding Marketplace: 5.506.000", question="Selisih?", tables=CHANNELS,
        claims=claims, language="id",
    ).accepted is accepted


def test_a_replacement_does_not_repeat_a_table_already_on_screen():
    result = finalize_verified_answer(
        "Medan 5, Jakarta 6, Bandung 7.", question="Penjualan per kota?", tables=CITIES,
        tables_shown=True, language="id",
    )
    assert result.replaced
    assert "| city |" not in result.text
    assert result.text.startswith("Angka berikut diambil langsung dari hasil query.")


def test_a_replacement_uses_the_users_language_and_number_format():
    result = finalize_verified_answer(
        "Medan 5, Jakarta 6, Bandung 7.", question="都市別の売上", tables={
            "e1": {"columns": ["city", "total_revenue"],
                   "rows": [["Jakarta", 900500], ["Medan", 1200250]]},
        }, tables_shown=True, language="de",
    )
    assert result.replaced
    assert "1.200.250" in result.text  # German grouping


def test_mostly_verified_prose_is_kept_with_only_bad_numbers_removed():
    result = finalize_verified_answer(
        "Medan 1.200, Jakarta 900, Bandung 400, total 9.999.",
        question="Penjualan per kota?", tables=CITIES, language="id",
    )
    assert not result.replaced
    assert "Medan 1.200" in result.text and "9.999" not in result.text
    assert "[angka tidak terverifikasi]" in result.text


SCALAR = {"e1": {"columns": ["order_count"], "rows": [[878]]}}


def test_a_wrong_headline_is_rebuilt_as_a_sentence_not_reported_as_no_result():
    result = finalize_verified_answer(
        "Jumlah pesanan 1.424.",
        question="Berapa jumlah pesanan 3 bulan terakhir?", tables=SCALAR, tables_shown=True,
        language="id",
    )
    assert result.replaced
    assert "order count: 878." in result.text


def test_claimed_dates_are_not_values():
    answer = "Order count 878 dari 1 Januari sampai 31 Maret 2025."
    dates = (Claim(text="1", kind="date"), Claim(text="31", kind="date"))
    question = "Berapa jumlah pesanan Januari sampai Maret 2025?"
    assert check_numeric_answer(
        answer, question=question, tables=SCALAR, claims=dates, language="id"
    ).accepted
    assert not check_numeric_answer(
        "Order count 878 dari 1 Januari sampai 45 Maret.", question=question, tables=SCALAR,
        claims=(Claim(text="45", kind="date"),), language="id",
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
        question="Top 2 kanal", tables=SHARES, language="id",
    ).accepted


def test_percentages_in_any_wording_are_checked_by_value():
    for answer in (
        "Porsinya merata di kisaran 24 sampai 26 persen.",
        "Shares sit between 24 and 26 percent.",
        "シェアは24〜26パーセントです。",
    ):
        assert check_numeric_answer(answer, question="Porsi kanal?", tables=SHARES).accepted
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
        question="Top categories per city", tables=TOP_PER_CITY,
    ).accepted
    assert not check_numeric_answer(
        "Ada 7 kota dalam hasil ini.", question="Top categories per city",
        tables=TOP_PER_CITY,
    ).accepted


def test_the_claims_block_never_reaches_the_user():
    text, claims = split_claims(
        "Medan 1.200.\n\n<claims>[{\"text\": \"1.200\", \"value\": 1200, \"kind\": \"cell\", "
        "\"row_label\": \"Medan\"}]</claims>"
    )
    result = finalize_verified_answer(
        text, question="x", tables=CITIES, claims=claims, language="id"
    )
    assert "<claims>" not in result.text and result.check.accepted
