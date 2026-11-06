"""Readers see a result's question, not its SQL, and no "highest latitude"."""

from app.modules.assistant.answer_contract import verified_leaders
from app.modules.assistant.intelligence import display_title


def test_a_result_title_is_its_question_without_sql():
    summary = "question: Outlet di Medan dengan koordinat\nsql:\nSELECT `outlets`.`name`"
    assert display_title(summary) == "Outlet di Medan dengan koordinat"
    assert display_title("forecast completed\nsql:\nSELECT 1") == "forecast completed"
    assert "SELECT" not in display_title("question: x\nsql:\nSELECT 1")


def test_coordinates_and_ids_are_not_ranked_as_measures():
    table = {
        "columns": ["outlet_name", "latitude", "longitude", "cluster_id", "planned_units"],
        "rows": [["A", 3.6, 98.68, 1, 120], ["B", 3.5, 98.65, 2, 80]],
    }
    lines = verified_leaders({"e1": table}, language="en")
    text = " ".join(lines)
    assert "latitude" not in text and "longitude" not in text and "cluster" not in text
    assert "planned" in text


def test_an_answer_with_its_own_table_gets_no_repeated_leader_lines():
    from app.modules.assistant.answer_contract import finalize_verified_answer

    table = {"columns": ["driver", "planned_km"], "rows": [["A", 3740.54], ["B", 3134.69]]}
    answer = "| Driver | Planned |\n|---|---:|\n| A | 3740.54 |\n| B | 3134.69 |"
    verified = finalize_verified_answer(
        answer, question="compare drivers", tables={"e1": table}, compares_groups=True,
    )
    assert "highest" not in verified.text.casefold() and "tertinggi" not in verified.text
