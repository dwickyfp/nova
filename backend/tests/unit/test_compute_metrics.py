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
    # The combined table is its own evidence; the data result is not replaced.
    assert context.last_result is None
    context.evidence_tables["evidence_5"] = {
        "columns": outcome.table["columns"], "rows": outcome.table["rows"],
    }
    context.last_result = EXPENSE
    per_head = await compute_metrics_tool.run(ToolInvocation(
        tool_call_id="c", tool_name="compute_metrics",
        arguments={"operation": "ratio", "value_column": "total_expense",
                   "compare_column": "active_headcount"},
    ), context)
    # Only the combined table holds both columns, so the ratio continues from it.
    assert per_head.ok, per_head.error
    assert per_head.table["columns"][-1] == "total_expense_per_active_headcount"
    missing = await compute_metrics_tool.run(ToolInvocation(
        tool_call_id="c", tool_name="compute_metrics",
        arguments={"operation": "combine", "evidence_id": "evidence_3",
                   "with_evidence_id": "someone-else"},
    ), context)
    assert not missing.ok and missing.recoverable


TOTAL = {"columns": ["revenue"], "rows": [["175184000.00"]]}


def test_scale_computes_a_what_if_total():
    """Benchmark what_if-001: "175,184,000 plus 10%" instead of 192,702,400."""
    result = run(TOTAL, operation="scale", value_column="revenue", percent=10)
    assert result["rows"] == [["1", "175184000.0000", "192702400.0000", "17518400.0000"]]
    assert "192702400.0000" in result["summary"]
    assert run(TOTAL, operation="scale", value_column="revenue", factor=1.1)["rows"][0][2] \
        == "192702400.0000"


def test_scale_one_row_reports_the_scenario_total():
    """Benchmark session-016.t2: Jakarta doubled, every other city unchanged."""
    cities = {"columns": ["shipping_city", "revenue"],
              "rows": [["Jakarta", "30496000"], ["Bandung", "22298000"], ["Medan", "100"]]}
    result = run(cities, operation="scale", value_column="revenue", factor=2,
                 only_label="jakarta")
    assert result["rows"][0] == ["Jakarta", "30496000.0000", "60992000.0000", "30496000.0000"]
    assert result["rows"][1][2] == "22298000.0000"
    assert result["rows"][-1] == ["total", "52794100.0000", "83290100.0000", "30496000.0000"]


def test_scale_compounds_over_periods_and_refuses_guesses():
    weekly = run(TOTAL, operation="scale", value_column="revenue", percent=5, periods=2)
    assert weekly["rows"][0][2] == "193140360.0000"  # 175,184,000 x 1.05^2
    for arguments in ({}, {"factor": 2, "percent": 5}, {"factor": 2, "only_label": "Bali"},
                      {"factor": 2, "periods": 0}):
        with pytest.raises(ComputeError):
            run(TOTAL, operation="scale", value_column="revenue", **arguments)


def test_value_column_matching_is_forgiving_but_never_guesses_between_columns():
    assert run(TOTAL, operation="sum", value_column="Revenue")["rows"][0][0] == "175184000.0000"
    assert run(TOTAL, operation="sum", value_column="orders.revenue")["rows"][0][0] \
        == "175184000.0000"
    assert run(TOTAL, operation="sum")["rows"][0][0] == "175184000.0000"
    with pytest.raises(ComputeError):
        run(CITIES, operation="sum")  # two numeric columns: name one


async def test_an_earlier_turns_evidence_id_uses_the_restored_result():
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "scale", "value_column": "revenue", "evidence_id": "evidence_1",
            "percent": 10,
        }),
        SimpleNamespace(last_result=dict(TOTAL), evidence_tables={}),
    )
    assert outcome.ok, outcome.error
    assert outcome.table["rows"][0][2] == "192702400.0000"


@pytest.mark.parametrize(("arguments", "scenario"), [
    # Benchmark what_if-001: the model fills both fields with the same change.
    ({"factor": 1.1, "percent": 10}, "192702400.0000"),
    # A neutral value for the field it did not mean.
    ({"factor": 1.1, "percent": 0}, "192702400.0000"),
    ({"factor": 1, "percent": 10}, "192702400.0000"),
    ({"factor": 2, "percent": 100}, "350368000.0000"),
])
def test_scale_accepts_a_redundant_factor_and_percent(arguments, scenario):
    assert run(TOTAL, operation="scale", value_column="revenue", **arguments)["rows"][0][2] \
        == scenario


def test_scale_refuses_two_different_changes_and_says_how_to_fix_it():
    with pytest.raises(ComputeError, match="one of them"):
        run(TOTAL, operation="scale", value_column="revenue", factor=1.2, percent=10)


async def test_the_tool_records_which_redundant_scale_argument_it_dropped():
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "scale", "value_column": "revenue", "factor": 1.1, "percent": 10,
        }),
        SimpleNamespace(last_result=dict(TOTAL), evidence_tables={}),
    )
    assert outcome.ok, outcome.error
    assert outcome.metadata["dropped_arguments"] == {"percent": 10}


async def test_a_calculation_never_replaces_the_data_result():
    """Benchmark session-030: a percent change replaced the order list, so the
    chart of the order values that followed found only from/to/pct_change."""
    orders = {"columns": ["order_id", "revenue"],
              "rows": [[1008, "1499000.00"], [1013, "599000.00"]]}
    context = SimpleNamespace(last_result=orders, evidence_tables={"evidence_1": orders})
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "pct_change", "value_column": "revenue",
            "label_column": "order_id", "from_label": "1008", "to_label": "1013",
        }),
        context,
    )
    assert outcome.ok, outcome.error
    assert context.last_result is orders
    # The loop shows the calculation as its own table part.
    assert outcome.table["columns"] == ["from", "to", "revenue_pct_change"]
    assert outcome.metadata["evidence_kind"] == "derived"


CITY_AOV = {"columns": ["shipping_city", "average_order_value"],
            "rows": [["Semarang", "24999000.00000000"], ["Medan", "3999000.00000000"]]}
OVERALL_AOV = {"columns": ["average_order_value"], "rows": [["11678933.33333333"]]}


async def test_difference_against_one_figure_of_another_result():
    """Benchmark multi_step-002: the top city's AOV against the overall AOV,
    the same measure in two results."""
    context = SimpleNamespace(
        last_result=OVERALL_AOV,
        evidence_tables={"evidence_1": CITY_AOV, "evidence_2": OVERALL_AOV},
    )
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "difference", "value_column": "average_order_value",
            "evidence_id": "evidence_1", "with_evidence_id": "evidence_2",
            "to_label": "Semarang",
        }),
        context,
    )
    assert outcome.ok, outcome.error
    assert outcome.table["columns"] == [
        "shipping_city", "average_order_value", "average_order_value_reference",
        "average_order_value_difference",
    ]
    assert outcome.table["rows"] == [
        ["Semarang", "24999000.0000", "11678933.3333", "13320066.6667"],
    ]
    pct = await compute_metrics_tool.run(
        ToolInvocation("c2", "compute_metrics", {
            "operation": "pct_change", "evidence_id": "evidence_1",
            "with_evidence_id": "evidence_2",
        }),
        context,
    )
    assert pct.ok, pct.error
    assert [row[-1] for row in pct.table["rows"]] == ["114.0521%", "-65.7589%"]


async def test_comparing_with_a_figure_needs_a_one_row_result():
    context = SimpleNamespace(
        last_result=None, evidence_tables={"evidence_1": CITY_AOV, "evidence_2": CITY_AOV},
    )
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "difference", "evidence_id": "evidence_1",
            "with_evidence_id": "evidence_2",
        }),
        context,
    )
    assert not outcome.ok and outcome.recoverable
    assert "one-row result" in (outcome.error or "")


async def test_ratio_to_a_figure_of_the_same_measure_is_allowed():
    context = SimpleNamespace(
        last_result=None,
        evidence_tables={"evidence_1": CITY_AOV, "evidence_2": OVERALL_AOV},
    )
    outcome = await compute_metrics_tool.run(
        ToolInvocation("c1", "compute_metrics", {
            "operation": "ratio", "value_column": "average_order_value",
            "evidence_id": "evidence_1", "with_evidence_id": "evidence_2",
        }),
        context,
    )
    assert outcome.ok, outcome.error
    assert outcome.table["rows"][0][-1] == "214.0521"
