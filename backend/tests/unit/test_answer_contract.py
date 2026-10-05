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


def _headcount_tables() -> dict:
    return {"evidence_1": {"columns": ["department", "active_headcount"],
                           "rows": [["Sales", "23"], ["Finance", "22"], ["Engineering", "18"]]}}


def test_a_count_claim_for_a_counting_metric_verifies_as_its_cell() -> None:
    answer = "| Sales | 23 |\n| Finance | 22 |\n| Engineering | 18 |"
    claims = tuple(Claim(text=text, kind="count") for text in ("23", "22", "18"))
    check = check_numeric_answer(
        answer, question="Headcount by department", tables=_headcount_tables(), claims=claims,
    )
    assert check.accepted
    assert {claim.column for claim in check.claims} == {"active_headcount"}


def test_a_count_claim_that_matches_no_cell_or_row_count_stays_unsupported() -> None:
    check = check_numeric_answer(
        "Sales has 63 people.", question="Headcount by department",
        tables=_headcount_tables(), claims=(Claim(text="63", kind="count"),),
    )
    assert check.unsupported == ("63",)


def _expense_tables() -> dict:
    return {"evidence_1": {"columns": ["total_expense"], "rows": [["58851000000.00"]]}}


@pytest.mark.parametrize(("answer", "language"), [
    ("Total 58.851.000.000, dari 1 Januari sampai 31 Desember 2025.", "id"),
    ("Total 58,851,000,000 from January 1 to December 31, 2025.", "en"),
    ("Total 58,851,000,000 between 1 Jan and 31 Dec 2025.", "en"),
])
def test_a_spoken_date_is_not_an_unverified_number(answer, language) -> None:
    check = check_numeric_answer(
        answer, question="Total expense 2025?", tables=_expense_tables(), language=language,
    )
    assert check.accepted, check.unsupported


def test_a_number_beside_a_month_is_still_checked_when_it_is_not_a_day() -> None:
    check = check_numeric_answer(
        "Total 58.851.000.000, dengan 47 Desember transaksi.", question="Total expense 2025?",
        tables=_expense_tables(), language="id",
    )
    assert check.unsupported == ("47",)


def _average_tables() -> dict:
    rows = [["Senior", "23590909.09090909"], ["Lead", "33666666.66666667"]]
    return {"evidence_1": {"columns": ["job_level", "avg_monthly_salary"], "rows": rows}}


@pytest.mark.parametrize("written", ["23,590,909.09", "23,590,909"])
def test_a_computed_average_may_be_stated_rounded(written) -> None:
    check = check_numeric_answer(
        f"Senior averages {written}.", question="Average salary by level",
        tables=_average_tables(),
    )
    assert check.accepted, check.unsupported


def test_a_rounded_average_must_still_round_from_its_cell() -> None:
    check = check_numeric_answer(
        "Senior averages 23,590,910.", question="Average salary by level",
        tables=_average_tables(),
    )
    assert check.unsupported == ("23,590,910",)


def test_a_two_decimal_money_cell_is_still_stated_exactly() -> None:
    tables = {"evidence_1": {"columns": ["revenue"], "rows": [["1234.56"]]}}
    check = check_numeric_answer("Revenue was 1,235.", question="Revenue?", tables=tables)
    assert check.unsupported == ("1,235",)


def test_a_computed_average_renders_with_two_decimals() -> None:
    text, claims = render_with_claims(_average_tables(), language="en")
    assert "23,590,909.09" in text and "09090909" not in text
    assert check_numeric_answer(
        text, question="x", tables=_average_tables(), claims=claims,
    ).accepted


def _combined_tables() -> dict:
    return {
        "evidence_1": {"columns": ["department", "total_expense"],
                       "rows": [["Sales", "12404000000.00"], ["Finance", "11553000000.00"]]},
        "evidence_2": {"columns": ["department", "active_headcount"],
                       "rows": [["Sales", "23"], ["Finance", "22"]]},
    }


def test_a_value_claimed_under_another_specialists_evidence_id_binds_to_its_cell() -> None:
    answer = "| Sales | 12.404.000.000 | 23 |\n| Finance | 11.553.000.000 | 22 |"
    claims = (
        Claim(text="23", kind="cell", evidence_id="evidence_1", row_label="Sales"),
        Claim(text="22", kind="cell", evidence_id="evidence_1", row_label="Finance"),
    )
    check = check_numeric_answer(
        answer, question="Expense and headcount by department", tables=_combined_tables(),
        claims=claims, language="id",
    )
    assert check.accepted, check.unsupported
    assert {claim.evidence_id for claim in check.claims if claim.text in {"23", "22"}} == {
        "evidence_2"
    }


def test_a_misbound_value_must_still_belong_to_the_named_row() -> None:
    check = check_numeric_answer(
        "| Finance | 23 |", question="Headcount by department", tables=_combined_tables(),
        claims=(Claim(text="23", kind="cell", evidence_id="evidence_1", row_label="Finance"),),
        language="id",
    )
    assert check.unsupported == ("23",)


def test_a_small_computed_value_cannot_drop_to_one_decimal() -> None:
    tables = {"evidence_1": {"columns": ["mean"], "rows": [[1.234]]}}
    assert check_numeric_answer("Mean is 1.2.", question="Mean?", tables=tables).unsupported == (
        "1.2",
    )
    assert check_numeric_answer("Mean is 1.23.", question="Mean?", tables=tables).accepted


@pytest.mark.parametrize("claims", [
    (Claim(text="18", kind="cell", row_label="Sales"),),
    (Claim(text="18", kind="cell", row_label="Engineering", column="total_expense"),),
    (Claim(text="18", kind="position"),),
])
def test_a_true_sentence_survives_a_wrong_note_about_it(claims) -> None:
    tables = _headcount_tables()
    tables["evidence_0"] = {"columns": ["department", "total_expense"],
                            "rows": [["Engineering", "12145000000.00"]]}
    check = check_numeric_answer(
        "| Engineering | 12.145.000.000 | 18 |", question="Expense and headcount",
        tables=tables, claims=claims, language="id",
    )
    assert check.accepted, check.unsupported


def test_a_claimed_change_is_not_rechecked_without_its_claim() -> None:
    tables = {"evidence_1": {"columns": ["month", "revenue"],
                             "rows": [["2026-01", "100"], ["2026-02", "80"]]}}
    answer = "Revenue rose 20%."
    assert check_numeric_answer(answer, question="Revenue trend", tables=tables).accepted
    risen = Claim(text="20%", kind="derived", direction="up")
    assert not check_numeric_answer(
        answer, question="Revenue trend", tables=tables, claims=(risen,),
    ).accepted


def test_a_forecast_the_runtime_computed_is_evidence_the_answer_may_state() -> None:
    from app.modules.assistant.intelligence import EvidenceTracker

    evidence = EvidenceTracker()
    item = evidence.add("ml_execute", "forecast completed", table={
        "columns": ["timestamp", "prediction", "lower_bound"],
        "rows": [["2026-10-31T00:00:00+00:00", 4805000000.0, 2497114486.1684604]],
    })
    check = check_numeric_answer(
        "Prediksi Oktober 2026 sebesar 4.805.000.000, batas bawah 2.497.114.486.",
        question="Prediksi expense bulan depan", tables=evidence.business_tables, language="id",
    )
    assert check.accepted, check.unsupported
    assert {claim.evidence_id for claim in check.claims if claim.evidence_id} == {
        item.evidence_id
    }


def test_a_follow_up_may_repeat_the_year_the_user_named_earlier() -> None:
    from types import SimpleNamespace

    from app.modules.assistant.service import _asked

    thread = SimpleNamespace(messages=[
        SimpleNamespace(role="user", content="Berapa total expense tahun 2025?"),
        SimpleNamespace(role="assistant", content="Rp 58.851.000.000 (lihat 2031)."),
    ])
    asked = _asked(thread, "kalau dipecah per kategori?")
    tables = {"e1": {"columns": ["category", "total_expense"], "rows": [["Travel", "1200"]]}}
    answer = "Total expense tahun 2025 per kategori: Travel 1.200."
    assert check_numeric_answer(answer, question=asked, tables=tables, language="id").accepted
    assert not check_numeric_answer(
        answer, question="kalau dipecah per kategori?", tables=tables, language="id",
    ).accepted
    # Only what the user said counts, not an earlier answer.
    assert "2031" not in asked


def test_a_change_a_decomposition_lists_is_accepted_however_the_model_noted_it() -> None:
    tables = {"evidence_2": {"columns": ["component", "change"], "rows": [
        ['["Travel", "Finance"]', "224000000.00"], ['["Office", "Operations"]', "-237000000.00"],
    ]}}
    answer = "Travel di Finance naik 224.000.000, sedangkan Office di Operations turun 237.000.000."
    noted = (
        Claim(text="224.000.000", kind="derived", direction="up"),
        Claim(text="237.000.000", kind="derived", direction="down"),
    )
    assert check_numeric_answer(
        answer, question="Kenapa berubah?", tables=tables, claims=noted, language="id",
    ).accepted
    # The direction still has to be the cell's own.
    wrong = (noted[0], Claim(text="237.000.000", kind="derived", direction="up"))
    assert not check_numeric_answer(
        answer, question="Kenapa berubah?", tables=tables, claims=wrong, language="id",
    ).accepted
