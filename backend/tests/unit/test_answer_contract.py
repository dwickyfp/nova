"""The numeric check reads numbers and the model's claims, never the answer's words.

Value-level rules (cells, rounding, percent points, compact scales) hold in any
language with or without claims. Which row a number belongs to, the direction
of a change, and comparisons are checked when the model claims them.
"""

from decimal import Decimal

import pytest

from app.modules.assistant.answer_contract import (
    Claim,
    check_numeric_answer,
    render_with_claims,
    split_claims,
)


def cell(text, value, **source):
    return Claim(text=text, value=Decimal(str(value)), kind="cell", **source)


def test_numbers_match_result_cells_and_question_period() -> None:
    result = check_numeric_answer(
        "Omzet Juli 2026 sebesar Rp1.234 dengan margin 20%.",
        question="Berapa omzet Juli 2026?",
        tables={"evidence_1": {"columns": ["revenue", "margin_pct"], "rows": [[1234, 20]]}},
        language="id",
    )
    assert result.accepted
    assert {claim.evidence_id for claim in result.claims} == {None, "evidence_1"}


def test_uncomputed_percentage_and_wrong_number_are_rejected() -> None:
    result = check_numeric_answer(
        "Revenue rose 20% to 900.", question="What is revenue?",
        tables={"evidence_1": {"columns": ["revenue"], "rows": [[100]]}},
    )
    assert not result.accepted
    assert result.unsupported == ("20%", "900")


def test_a_claim_binds_a_number_to_its_row() -> None:
    table = {"evidence_1": {"columns": ["region", "revenue"],
                            "rows": [["East", 100], ["West", 200]]}}
    right = (cell("100", 100, row_label="East"), cell("200", 200, row_label="West"))
    wrong = (cell("200", 200, row_label="East"), cell("100", 100, row_label="West"))
    assert check_numeric_answer(
        "East: 100, West: 200.", question="Revenue by region?", tables=table, claims=right
    ).accepted
    result = check_numeric_answer(
        "East: 200, West: 100.", question="Revenue by region?", tables=table, claims=wrong
    )
    assert result.unsupported == ("200", "100")


def test_a_claim_whose_value_differs_from_the_text_is_rejected() -> None:
    table = {"evidence_1": {"columns": ["revenue"], "rows": [[100]]}}
    assert not check_numeric_answer(
        "Revenue is 100.", question="Revenue?", tables=table, claims=(cell("100", 120),)
    ).accepted


def test_question_number_needs_a_question_claim_or_a_year() -> None:
    table = {"evidence_1": {"columns": ["revenue"], "rows": [[80]]}}
    assert check_numeric_answer(
        "Revenue in 2026 is 80.", question="Revenue in 2026?", tables=table
    ).accepted
    # A number that only repeats the question is not presented as a result.
    assert not check_numeric_answer(
        "Revenue is 100.", question="Is revenue 100?", tables=table,
        claims=(cell("100", 100),),
    ).accepted


def test_a_claimed_decrease_uses_the_size_of_a_negative_change() -> None:
    table = {"evidence_1": {"columns": ["net_change"], "rows": [[-20]]}}
    down = (Claim(text="20", value=Decimal(20), kind="cell", direction="down"),)
    assert check_numeric_answer(
        "Revenue fell by 20.", question="Why did revenue fall?", tables=table, claims=down
    ).accepted
    assert not check_numeric_answer(
        "Revenue changed by 20.", question="Why did revenue fall?", tables=table
    ).accepted
    assert check_numeric_answer(
        "Revenue changed by -20.", question="Why did revenue fall?", tables=table
    ).accepted


def test_rounded_percent_and_compact_scales_in_indonesian() -> None:
    table = {
        "evidence_1": {
            "columns": ["sales_channel", "recognized_revenue", "gross_margin_pct", "order_count"],
            "rows": [
                ["WhatsApp B2B", "847526929200.00", "0.37028179", 44266],
                ["Mobile App", "3368049065451.00", "0.36962540", 177450],
            ],
        }
    }
    answer = (
        "WhatsApp B2B: recognized revenue Rp847,53 miliar, gross margin 37,03%, "
        "order count 44.266. Mobile App: recognized revenue Rp3,37 triliun, "
        "gross margin 36,96%, order count 177.450."
    )
    question = "Bandingkan per channel"
    assert check_numeric_answer(answer, question=question, tables=table, language="id").accepted
    wrong = answer.replace("Rp847,53", "Rp847,59")
    assert "847,59 miliar" in check_numeric_answer(
        wrong, question=question, tables=table, language="id"
    ).unsupported


@pytest.mark.parametrize(("language", "answer"), [
    ("en", "Revenue was 3.37 trillion."),
    ("de", "Der Umsatz betrug 3,37 Billionen."),
    ("es", "Los ingresos fueron 3,37 billones."),
    ("ja", "売上は3.37兆でした。"),
    ("zh", "收入为3.37万亿。"),
    ("ko", "매출은 3.37조였습니다."),
    ("ar", "بلغت الإيرادات ٣٫٣٧ ترليون."),
])
def test_compact_scales_are_read_from_locale_data(language, answer) -> None:
    table = {"evidence_1": {"columns": ["revenue"], "rows": [["3368049065451.00"]]}}
    assert check_numeric_answer(
        answer, question="revenue", tables=table, language=language
    ).accepted
    wrong = {"evidence_1": {"columns": ["revenue"], "rows": [["3968049065451.00"]]}}
    assert not check_numeric_answer(
        answer, question="revenue", tables=wrong, language=language
    ).accepted


def test_a_unit_word_outside_the_locale_data_is_read_through_its_claim() -> None:
    # CLDR spells trillion "ترليون"; models often write "تريليون". The claim gives the value, which
    # must be the written number times a power of ten and match a cell.
    answer = "بلغت الإيرادات ٣٫٣٧ تريليون."
    table = {"evidence_1": {"columns": ["revenue"], "rows": [["3368049065451.00"]]}}
    claim = Claim(text="٣٫٣٧ تريليون", value=Decimal("3370000000000"), kind="cell")
    assert check_numeric_answer(
        answer, question="revenue", tables=table, claims=(claim,), language="ar"
    ).accepted
    # Claiming billions for a trillion-sized cell does not match the data.
    invented = Claim(text=claim.text, value=Decimal("3370000000"), kind="cell")
    assert not check_numeric_answer(
        answer, question="revenue", tables=table, claims=(invented,), language="ar"
    ).accepted
    assert not check_numeric_answer(
        answer, question="revenue", tables=table, language="ar"
    ).accepted


def test_rounded_percent_matches_a_percent_point_cell_as_well_as_a_ratio() -> None:
    table = {"evidence_1": {"columns": ["channel", "gross_margin_pct"],
                            "rows": [["Direct", "37.028179"], ["Partner", "0.36962540"]]}}
    assert check_numeric_answer(
        "Direct gross margin 37.03%; Partner gross margin 36.96%.",
        question="Compare margins by channel", tables=table,
    ).accepted
    assert not check_numeric_answer(
        "Direct gross margin 36.95%.", question="Compare margins by channel", tables=table,
    ).accepted


def test_percent_magnitude_distinguishes_ratio_from_percent_points() -> None:
    table = {"evidence_1": {"columns": ["channel", "gross_margin_pct"],
                            "rows": [["Ratio", "0.37"], ["Points", "37.028179"],
                                     ["Tiny", "0.5%"]]}}
    question = "Compare margin by channel"
    assert check_numeric_answer(
        "Ratio gross margin 37%; Points gross margin 37.03%; Tiny gross margin 0.5%.",
        question=question, tables=table,
    ).accepted
    for answer in ("Points gross margin 3702.82%.", "Tiny gross margin 50%."):
        assert not check_numeric_answer(answer, question=question, tables=table).accepted


def test_percent_decimal_separator_cannot_match_a_grouped_integer() -> None:
    question = "What was the margin?"
    huge = {"evidence_1": {"columns": ["gross_margin_pct"], "rows": [[37028]]}}
    points = {"evidence_1": {"columns": ["gross_margin_pct"], "rows": [[37.028]]}}
    ratio = {"evidence_1": {"columns": ["gross_margin_pct"], "rows": [[0.37028]]}}
    for literal in ("37,028%", "37.028%"):
        answer = f"Gross margin was {literal}."
        assert not check_numeric_answer(answer, question=question, tables=huge).accepted
        assert check_numeric_answer(answer, question=question, tables=points).accepted
        assert check_numeric_answer(answer, question=question, tables=ratio).accepted


def test_three_digit_separator_with_a_scale() -> None:
    low = {"evidence_1": {"columns": ["revenue"], "rows": [[1_234_000]]}}
    high = {"evidence_1": {"columns": ["revenue"], "rows": [[1_234_000_000]]}}
    assert not check_numeric_answer(
        "Revenue Rp1.234 juta.", question="Berapa revenue?", tables=low, language="id"
    ).accepted
    assert check_numeric_answer(
        "Revenue Rp1.234 juta.", question="Berapa revenue?", tables=high, language="id"
    ).accepted
    assert check_numeric_answer(
        "Revenue 1.234 million.", question="How much revenue?", tables=low
    ).accepted


def test_compact_scale_consumes_the_whole_numeric_token() -> None:
    real = {"evidence_1": {"columns": ["revenue"], "rows": [[9_990_000_000]]}}
    wrong = {"evidence_1": {"columns": ["revenue"], "rows": [[9]]}}
    assert check_numeric_answer("Revenue Rp9.99B.", question="How much?", tables=real).accepted
    assert not check_numeric_answer(
        "Revenue Rp9.99B.", question="How much?", tables=wrong
    ).accepted
    assert check_numeric_answer("Revenue Rp9.990B.", question="How much?", tables=real).accepted
    assert not check_numeric_answer(
        "Revenue Rp9.991B.", question="How much?", tables=real
    ).accepted


def test_question_year_does_not_exempt_a_scaled_amount() -> None:
    table = {"evidence_1": {"columns": ["revenue"], "rows": [[100]]}}
    assert not check_numeric_answer(
        "Revenue was 2025 billion.", question="What was revenue in 2025?", tables=table
    ).accepted


def test_the_claims_block_is_split_from_the_answer() -> None:
    text, claims = split_claims(
        "Revenue was 1,2 juta.\n<claims>[{\"text\": \"1,2 juta\", \"value\": 1200000, "
        "\"kind\": \"cell\", \"row_label\": \"Jakarta\", \"direction\": \"sideways\"}]</claims>"
    )
    assert text == "Revenue was 1,2 juta."
    assert claims[0].value == Decimal(1200000)
    assert claims[0].row_label == "Jakarta"
    assert claims[0].direction is None
    assert split_claims("No block here.") == ("No block here.", None)
    assert split_claims("Text <claims>not json</claims>") == ("Text", ())


@pytest.mark.parametrize("language", ["en", "id", "ja", "de", "ar", "hi"])
def test_a_rendering_verifies_itself_in_every_language(language) -> None:
    tables = {
        "evidence_1": {
            "columns": ["sales_channel", "recognized_revenue", "gross_margin_pct", "order_count"],
            "rows": [
                ["Mobile App", "3368049065451.00", "0.36962540", 177450],
                ["Store", "3013024030096.50", "0.36952650", 158569],
                ["WhatsApp B2B", "847526929200.00", "0.37028179", 44266],
            ],
        }
    }
    text, claims = render_with_claims(tables, language=language)
    assert check_numeric_answer(
        text, question="x", tables=tables, claims=claims, language=language
    ).accepted
    assert "Mobile App" in text and "WhatsApp B2B" in text


def test_indonesian_rendering_lists_every_group_highest_first() -> None:
    tables = {
        "evidence_1": {
            "columns": ["sales_channel", "recognized_revenue", "gross_margin_pct", "order_count"],
            "rows": [
                ["Mobile App", "3368049065451.00", "0.36962540", 177450],
                ["Store", "3013024030096.50", "0.36952650", 158569],
                ["Marketplace", "2893685878419.00", "0.36990534", 152078],
                ["Website", "1935135942274.50", "0.36930145", 101418],
                ["WhatsApp B2B", "847526929200.00", "0.37028179", 44266],
            ],
        }
    }
    text, _claims = render_with_claims(tables, language="id")
    assert "recognized revenue: Mobile App 3.368.049.065.451, Store" in text
    assert "gross margin pct: WhatsApp B2B 37,03%" in text
    assert "Website 36,93%." in text
    assert "order count: Mobile App 177.450" in text
    assert "1.935.135.942.274,50" in text
