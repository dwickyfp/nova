"""Readers see a result's question, not its SQL, and verified prose stands as written."""

import pytest

from app.modules.assistant.answer_contract import finalize_verified_answer
from app.modules.assistant.intelligence import display_title


def test_a_result_title_is_its_question_without_sql():
    summary = "question: Outlet di Medan dengan koordinat\nsql:\nSELECT `outlets`.`name`"
    assert display_title(summary) == "Outlet di Medan dengan koordinat"
    assert display_title("forecast completed\nsql:\nSELECT 1") == "forecast completed"
    assert "SELECT" not in display_title("question: x\nsql:\nSELECT 1")


@pytest.mark.parametrize(
    ("language", "answer"),
    [
        ("en", "Yogyakarta had the highest revenue at 31,498,000."),
        ("id", "Yogyakarta memiliki pendapatan tertinggi, 31,498,000."),
    ],
)
def test_a_verified_comparison_gets_no_appended_leader_lines(language, answer):
    table = {
        "columns": ["shipping_city", "revenue", "order_count"],
        "rows": [["Yogyakarta", 31498000, 2], ["Jakarta", 30496000, 3], ["Palembang", 599000, 1]],
    }
    verified = finalize_verified_answer(
        answer, question="compare cities", tables={"e1": table}, compares_groups=True,
        language=language,
    )
    assert verified.text == answer
    assert "lowest" not in verified.text and "terendah" not in verified.text
    assert "order count:" not in verified.text
