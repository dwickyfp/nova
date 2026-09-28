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
