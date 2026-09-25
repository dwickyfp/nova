from app.modules.assistant.answer_contract import (
    check_numeric_answer,
    is_numeric_comparison_question,
    render_verified_comparison,
)


def test_numbers_match_result_cells_and_question_period() -> None:
    result = check_numeric_answer(
        "Omzet Juli 2026 sebesar Rp1.234 dengan margin 20%.",
        question="Berapa omzet Juli 2026?",
        tables={
            "evidence_1": {
                "columns": ["revenue", "margin_pct"],
                "rows": [[1234, 20]],
            }
        },
    )
    assert result.accepted
    assert {claim.evidence_id for claim in result.claims} == {None, "evidence_1"}


def test_uncomputed_percentage_and_wrong_number_are_rejected() -> None:
    result = check_numeric_answer(
        "Revenue rose 20% to 900.",
        question="What is revenue?",
        tables={"evidence_1": {"columns": ["revenue"], "rows": [[100]]}},
    )
    assert not result.accepted
    assert result.unsupported == ("20%", "900")


def test_number_must_match_the_named_result_row() -> None:
    table = {
        "evidence_1": {
            "columns": ["region", "revenue"],
            "rows": [["East", 100], ["West", 200]],
        }
    }
    assert check_numeric_answer(
        "East: 100, West: 200.", question="Revenue by region?", tables=table
    ).accepted
    wrong = check_numeric_answer(
        "East: 200, West: 100.", question="Revenue by region?", tables=table
    )
    assert not wrong.accepted
    assert wrong.unsupported == ("200", "100")


def test_question_number_is_not_automatically_a_result() -> None:
    table = {"evidence_1": {"columns": ["revenue"], "rows": [[80]]}}
    assert not check_numeric_answer(
        "Revenue is 100.", question="Is revenue 100?", tables=table
    ).accepted
    assert check_numeric_answer(
        "Revenue in 2026 is 80.", question="Revenue in 2026?", tables=table
    ).accepted


def test_decrease_can_use_the_magnitude_of_a_negative_delta() -> None:
    table = {"evidence_1": {"columns": ["net_change"], "rows": [[-20]]}}
    assert check_numeric_answer(
        "Revenue fell by 20.", question="Why did revenue fall?", tables=table
    ).accepted
    assert not check_numeric_answer(
        "Revenue changed by 20.", question="Why did revenue fall?", tables=table
    ).accepted


def test_rounded_percent_and_explicit_money_scale_match_the_named_row() -> None:
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
    assert check_numeric_answer(answer, question="Bandingkan per channel", tables=table).accepted
    wrong = answer.replace(
        "WhatsApp B2B: recognized revenue Rp847,53",
        "WhatsApp B2B: recognized revenue Rp847,59",
    )
    assert "847,59" in check_numeric_answer(
        wrong, question="Bandingkan per channel", tables=table
    ).unsupported


def test_rounded_percent_matches_a_percent_point_cell_as_well_as_a_ratio() -> None:
    table = {
        "evidence_1": {
            "columns": ["channel", "gross_margin_pct"],
            "rows": [["Direct", "37.028179"], ["Partner", "0.36962540"]],
        }
    }
    assert check_numeric_answer(
        "Direct gross margin 37.03%; Partner gross margin 36.96%.",
        question="Compare margins by channel",
        tables=table,
    ).accepted
    assert not check_numeric_answer(
        "Direct gross margin 36.96%.",
        question="Compare margins by channel",
        tables=table,
    ).accepted


def test_percent_magnitude_distinguishes_ratio_from_percent_points() -> None:
    table = {
        "evidence_1": {
            "columns": ["channel", "gross_margin_pct"],
            "rows": [["Ratio", "0.37"], ["Points", "37.028179"], ["Tiny", "0.5%"]],
        }
    }
    question = "Compare margin by channel"
    assert check_numeric_answer(
        "Ratio gross margin 37%; Points gross margin 37.03%; Tiny gross margin 0.5%.",
        question=question,
        tables=table,
    ).accepted
    for answer in (
        "Ratio gross margin 0.37%.",
        "Points gross margin 3702.82%.",
        "Tiny gross margin 50%.",
    ):
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
    assert not check_numeric_answer(
        "Gross margin was 37028%.", question=question, tables=points
    ).accepted


def test_row_label_carries_across_comma_until_the_next_label() -> None:
    table = {
        "evidence_1": {
            "columns": ["region", "revenue", "margin_pct"],
            "rows": [["East", 100, 10], ["West", 200, 20]],
        }
    }
    assert check_numeric_answer(
        "East: revenue 100, margin 10%. West: revenue 200, margin 20%.",
        question="Compare revenue and margin by region",
        tables=table,
    ).accepted
    assert not check_numeric_answer(
        "East: revenue 100, margin 20%.",
        question="Compare revenue and margin by region",
        tables=table,
    ).accepted


def test_three_digit_separator_uses_spelled_scale_locale() -> None:
    low = {"evidence_1": {"columns": ["revenue"], "rows": [[1_234_000]]}}
    high = {"evidence_1": {"columns": ["revenue"], "rows": [[1_234_000_000]]}}
    unscaled = {"evidence_1": {"columns": ["revenue"], "rows": [[1_234]]}}
    assert not check_numeric_answer(
        "Revenue Rp1.234 juta.", question="Berapa revenue?", tables=low
    ).accepted
    assert check_numeric_answer(
        "Revenue Rp1.234 juta.", question="Berapa revenue?", tables=high
    ).accepted
    assert not check_numeric_answer(
        "Revenue Rp1.234 juta.", question="Berapa revenue?", tables=unscaled
    ).accepted
    assert check_numeric_answer(
        "Revenue 1.234 million.", question="How much revenue?", tables=low
    ).accepted


def test_compact_scale_consumes_the_whole_numeric_token() -> None:
    real = {"evidence_1": {"columns": ["revenue"], "rows": [[9_990_000_000]]}}
    wrong = {"evidence_1": {"columns": ["revenue"], "rows": [[9]]}}
    unscaled = {"evidence_1": {"columns": ["revenue"], "rows": [[9.99]]}}
    assert check_numeric_answer(
        "Revenue Rp9.99B.", question="How much revenue?", tables=real
    ).accepted
    assert not check_numeric_answer(
        "Revenue Rp9.99B.", question="How much revenue?", tables=wrong
    ).accepted
    assert not check_numeric_answer(
        "Revenue Rp9.99B.", question="How much revenue?", tables=unscaled
    ).accepted
    assert not check_numeric_answer(
        "Revenue Rp9.99XYZ.", question="How much revenue?", tables=wrong
    ).accepted
    assert not check_numeric_answer(
        "Revenue Rp9.990B.", question="How much revenue?", tables=real
    ).accepted


def test_question_year_does_not_exempt_a_scaled_amount() -> None:
    table = {"evidence_1": {"columns": ["revenue"], "rows": [[100]]}}
    assert not check_numeric_answer(
        "Revenue was 2025 billion.", question="What was revenue in 2025?", tables=table
    ).accepted


def test_rejected_sales_draft_gets_an_exact_authorized_comparison() -> None:
    question = (
        "Bandingkan recognized revenue, gross margin, "
        "dan order count per channel untuk 2025."
    )
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
    assert not check_numeric_answer(
        "Mobile App gross margin 42%.", question=question, tables=tables
    ).accepted
    replacement = render_verified_comparison(tables, question=question)
    assert check_numeric_answer(replacement, question=question, tables=tables).accepted
    assert is_numeric_comparison_question(question, tables)
    assert "Mobile App" in replacement and "WhatsApp B2B" in replacement
    assert "recognized revenue: tertinggi Mobile App; terendah WhatsApp B2B" in replacement
    assert "gross margin pct: tertinggi WhatsApp B2B; terendah Website" in replacement
    assert "order count: tertinggi Mobile App; terendah WhatsApp B2B" in replacement
    assert "3.368.049.065.451,00" in replacement
    assert "847.526.929.200,00" in replacement
    assert "37,03%" in replacement and "177.450" in replacement
    assert "42%" not in replacement
