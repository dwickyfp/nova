"""compute_metrics: exact arithmetic over an authorized result, never new data."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.tools.compute_metrics import ComputeError, compute, compute_metrics_tool
from app.modules.assistant.answer_contract import check_numeric_answer
from app.modules.assistant.tools import ToolInvocation

MONTHS = {
    "columns": ["month", "total_revenue"],
    "rows": [["2026-06", 1000000], ["2026-07", 1200000], ["2026-08", 1500000]],
}
CITIES = {
    "columns": ["city", "revenue_2025", "revenue_2026"],
    "rows": [["Jakarta", 600, 900], ["Bandung", 300, 250], ["Surabaya", 100, 150]],
}


def run(table, **arguments):
    return compute(table, arguments)


def test_pct_change_between_two_labels():
    result = run(MONTHS, operation="pct_change", value_column="total_revenue",
                 from_label="2026-07", to_label="2026-08")
    assert result["rows"] == [["2026-07", "2026-08", "25.0000%"]]


def test_row_over_row_growth_keeps_result_order():
    result = run(MONTHS, operation="pct_change", value_column="total_revenue")
    assert [row[2] for row in result["rows"]] == [None, "20.0000%", "25.0000%"]


def test_difference_share_rank_and_aggregates():
    assert run(MONTHS, operation="difference", value_column="total_revenue",
               from_label="2026-06", to_label="2026-08")["rows"][0][2] == "500000.0000"
    shares = run(CITIES, operation="share_of_total", value_column="revenue_2026")
    assert [row[2] for row in shares["rows"]] == ["69.2308%", "19.2308%", "11.5385%"]
    ranked = run(CITIES, operation="rank", value_column="revenue_2025")
    assert [row[1] for row in ranked["rows"]] == ["Jakarta", "Bandung", "Surabaya"]
    assert run(MONTHS, operation="sum", value_column="total_revenue")["rows"][0][0] == (
        "3700000.0000"
    )
    assert run(MONTHS, operation="avg", value_column="total_revenue")["rows"][0][0] == (
        "1233333.3333"
    )


def test_cagr_and_contribution():
    cagr = run(MONTHS, operation="cagr", value_column="total_revenue",
               from_label="2026-06", to_label="2026-08")
    assert cagr["rows"][0][2:] == [2, "22.4745%"]
    contribution = run(CITIES, operation="contribution", value_column="revenue_2026",
                       compare_column="revenue_2025")
    assert contribution["rows"] == [
        ["Jakarta", "300.0000", "100.0000%"],
        ["Bandung", "-50.0000", "-16.6667%"],
        ["Surabaya", "50.0000", "16.6667%"],
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "pct_change", "value_column": "missing"},
        {"operation": "pct_change", "value_column": "total_revenue", "from_label": "2020-01",
         "to_label": "2026-08"},
        {"operation": "median", "value_column": "total_revenue"},
        {"operation": "contribution", "value_column": "total_revenue"},
        {"operation": "total_revenue", "value_column": "month"},
    ],
)
def test_invalid_requests_are_errors_not_guesses(arguments):
    with pytest.raises(ComputeError):
        compute(MONTHS, arguments)


def test_zero_division_is_reported():
    table = {"columns": ["m", "v"], "rows": [["a", 0], ["b", 5]]}
    with pytest.raises(ComputeError):
        compute(table, {"operation": "pct_change", "value_column": "v",
                        "from_label": "a", "to_label": "b"})


async def test_tool_reads_the_turn_evidence_and_its_output_verifies_the_answer():
    context = SimpleNamespace(last_result=None, evidence_tables={"evidence_1": MONTHS})
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "pct_change", "value_column": "total_revenue",
            "evidence_id": "evidence_1", "from_label": "2026-07", "to_label": "2026-08",
        }),
        context,
    )
    assert outcome.ok
    assert outcome.metadata["evidence_kind"] == "derived"
    tables = {"evidence_1": MONTHS, "evidence_2": outcome.table}
    assert check_numeric_answer(
        "Revenue Agustus tumbuh 25% dibanding Juli.", question="Growth Agustus?", tables=tables,
    ).accepted


async def test_tool_without_a_result_asks_for_data_first():
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {"operation": "sum", "value_column": "x"}),
        SimpleNamespace(last_result=None, evidence_tables={}),
    )
    assert not outcome.ok and outcome.error_class == "NO_DATA_RESULT" and outcome.recoverable


async def test_unknown_evidence_id_lists_the_available_ones():
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "sum", "value_column": "total_revenue", "evidence_id": "evidence_9",
        }),
        SimpleNamespace(last_result=None, evidence_tables={"evidence_1": MONTHS}),
    )
    assert not outcome.ok
    assert outcome.repair_context == {"evidence_ids": ["evidence_1"]}


def test_date_labels_match_their_midnight_timestamps_and_errors_list_labels():
    table = {"columns": ["order_date", "total_revenue"],
             "rows": [["2026-07-01 00:00:00", 100], ["2026-08-01 00:00:00", 125]]}
    result = compute(table, {"operation": "pct_change", "value_column": "total_revenue",
                             "from_label": "2026-07-01", "to_label": "2026-08-01"})
    assert result["rows"][0][2] == "25.0000%"
    with pytest.raises(ComputeError, match="Available: '2026-07-01 00:00:00'"):
        compute(table, {"operation": "pct_change", "value_column": "total_revenue",
                        "from_label": "2026-02-01", "to_label": "2026-08-01"})


EXPENSE = {"columns": ["department", "total_expense"],
           "rows": [["Sales", "12404000000.00"], ["Finance", "11553000000.00"], ["Legal", "5.00"]]}
HEADCOUNT = {"columns": ["Department", "active_headcount"],
             "rows": [["finance", 22], ["Sales", 23], ["Support", 4]]}


def test_combine_joins_two_results_on_their_shared_label_only_where_both_have_it():
    from app.modules.agents.tools.compute_metrics import combine

    result = combine(EXPENSE, HEADCOUNT)
    assert result["columns"] == ["department", "total_expense", "active_headcount"]
    assert result["rows"] == [["Sales", "12404000000.00", 23], ["Finance", "11553000000.00", 22]]
    assert "2 row(s) had no match" in result["summary"]


@pytest.mark.parametrize(("second", "message"), [
    ({"columns": ["region", "active_headcount"], "rows": [["West", 3]]}, "share exactly one"),
    ({"columns": ["department", "active_headcount"], "rows": [["Sales", 1], ["sales", 2]]},
     "more than one row"),
    ({"columns": ["department", "active_headcount"], "rows": [["Support", 4]]}, "appears in both"),
])
def test_combine_never_guesses_a_join(second, message):
    from app.modules.agents.tools.compute_metrics import ComputeError, combine

    with pytest.raises(ComputeError, match=message):
        combine(EXPENSE, second)


def test_ratio_divides_one_column_by_another_and_leaves_a_zero_divisor_empty():
    from app.modules.agents.tools.compute_metrics import compute

    table = {"columns": ["department", "total_expense", "active_headcount"],
             "rows": [["Sales", "1000", 4], ["Finance", "900", 0]]}
    result = compute(table, {"operation": "ratio", "value_column": "total_expense",
                             "compare_column": "active_headcount"})
    assert result["columns"][-1] == "total_expense_per_active_headcount"
    assert result["rows"] == [["Sales", "1000.0000", "4.0000", "250.0000"],
                              ["Finance", "900.0000", "0.0000", None]]


@pytest.mark.asyncio
async def test_the_tool_combines_two_held_results_and_continues_from_the_new_table():
    from types import SimpleNamespace

    from app.modules.agents.tools.compute_metrics import compute_metrics_tool
    from app.modules.assistant.tools import ToolInvocation

    context = SimpleNamespace(
        evidence_tables={"evidence_3": EXPENSE, "evidence_4": HEADCOUNT}, last_result=None,
    )
    outcome = await compute_metrics_tool.run(ToolInvocation(
        tool_call_id="c", tool_name="compute_metrics",
        arguments={"operation": "combine", "evidence_id": "evidence_3",
                   "with_evidence_id": "evidence_4"},
    ), context)
    assert outcome.ok and outcome.table["columns"][-1] == "active_headcount"
    assert context.last_result["rows"] == outcome.table["rows"]
    missing = await compute_metrics_tool.run(ToolInvocation(
        tool_call_id="c", tool_name="compute_metrics",
        arguments={"operation": "combine", "evidence_id": "evidence_3",
                   "with_evidence_id": "someone-else"},
    ), context)
    assert not missing.ok and missing.recoverable
