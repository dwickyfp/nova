"""The repair for an answer given before the required tools ran."""

from types import SimpleNamespace

from app.modules.assistant.service import _missing_required_prompt


def _evidence(*tables):
    return SimpleNamespace(
        business_tables={f"evidence_{i}": table for i, table in enumerate(tables)},
    )


def test_an_empty_query_result_asks_for_the_query_again_without_added_filters():
    prompt = _missing_required_prompt(
        ["ml_execute"], _evidence({"columns": ["outlet_name"], "rows": []}),
    )
    assert prompt.startswith("This turn requires ml_execute before answering.")
    assert "returned no rows" in prompt and "drop any you added" in prompt


def test_rows_or_no_query_yet_keep_the_plain_repair():
    with_rows = _missing_required_prompt(
        ["ml_execute"], _evidence({"columns": ["outlet_name"], "rows": [["A"]]}),
    )
    nothing_yet = _missing_required_prompt(["semantic_query"], _evidence())
    for prompt in (with_rows, nothing_yet):
        assert "returned no rows" not in prompt
        assert prompt.endswith("Do not answer yet.")


def test_only_a_missing_derived_step_is_answered_from_verified_rows():
    # Live run 2026-11-16: a calculation failed after the query and the turn ended empty.
    from app.modules.assistant.service import _DERIVED_STEP_MISSING_PROMPT, _answer_from_results

    rows = _evidence({"columns": ["region", "revenue"], "rows": [["Jakarta", 1]]})
    failed = {"compute_metrics", "data_to_chart", "semantic_query"}
    assert _answer_from_results(["compute_metrics"], rows, failed)
    assert _answer_from_results(["data_to_chart", "compute_metrics"], rows, failed)
    # A step the model never ran is not excused: the turn still fails.
    assert not _answer_from_results(["compute_metrics"], rows, set())
    # Without verified rows, or with the data step itself missing, there is nothing to answer from.
    assert not _answer_from_results(["compute_metrics"], _evidence(), failed)
    assert not _answer_from_results(["semantic_query", "compute_metrics"], rows, failed)
    assert not _answer_from_results([], rows, failed)
    assert "do not compute it yourself" in _DERIVED_STEP_MISSING_PROMPT
