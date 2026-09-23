from app.modules.assistant.answer_contract import check_numeric_answer


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
