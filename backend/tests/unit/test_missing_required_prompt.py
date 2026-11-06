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
