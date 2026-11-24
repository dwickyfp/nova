"""A calculation's table never replaces the data it was computed from.

Built from benchmark session-030 (recorded turns t1 and t3): t1 listed the
orders by value and computed one percent change; t3 asked "Tampilkan
distribusinya" and the chart found only the from/to/pct_change table.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agents.tools import data_to_chart as chart_module
from app.modules.agents.tools.data_to_chart import data_to_chart_tool
from app.modules.assistant.service import _latest_thread_result
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolInvocation

QUERY_CALL = "bfcd5f1f-92d0-4264-838b-1c9bbb60706b"
COMPUTE_CALL = "ca1dd398-462a-43a8-a40d-fbba61228e0d"
ORDERS_SQL = (
    "SELECT\n  `orders`.`order_id` AS `order_id`,\n  SUM(`orders`.`total_amount`) AS "
    "`revenue`\nFROM `NOVA_DEMO`.`orders` AS `orders`\nGROUP BY `orders`.`order_id`\n"
    "ORDER BY `revenue` DESC"
)
ORDER_ROWS = [
    [1005, "24999000.00"], [1015, "24999000.00"], [1001, "23298000.00"],
    [1012, "22499000.00"], [1007, "19999000.00"], [1003, "18999000.00"],
    [1009, "12999000.00"], [1006, "6499000.00"], [1002, "4299000.00"],
    [1014, "4299000.00"], [1010, "3999000.00"], [1011, "3299000.00"],
    [1004, "2899000.00"], [1008, "1499000.00"], [1013, "599000.00"],
]


def _session_030_t1(*, with_orders: bool = True) -> AssistantThread:
    steps: list[dict] = [
        {"kind": "tool", "tool_call_id": QUERY_CALL, "name": "semantic_query",
         "status": "done", "preview": ORDERS_SQL},
        {"kind": "tool", "tool_call_id": COMPUTE_CALL, "name": "compute_metrics",
         "status": "done", "preview": "Calculate the pct change of revenue"},
        {"kind": "text", "text": "Pesanan #1013 bernilai 599.000."},
    ]
    if with_orders:
        steps.append({"kind": "table", "title": "Ada pesanan yang nilainya tidak biasa?",
                      "columns": ["order_id", "revenue"], "rows": ORDER_ROWS,
                      "tool_call_id": QUERY_CALL})
    steps.append({"kind": "table", "title": "pct_change of revenue",
                  "columns": ["from", "to", "revenue_pct_change"],
                  "rows": [["1008", "1013", "-60.0400%"]], "tool_call_id": COMPUTE_CALL})
    return AssistantThread(
        thread_id="session-030", user_name="alice", title="session-030",
        messages=[
            AssistantMessage(message_id="u1", role="user",
                             content="Ada pesanan yang nilainya tidak biasa?"),
            AssistantMessage(message_id="a1", role="assistant", content="", steps=steps),
            AssistantMessage(message_id="u3", role="user", content="Tampilkan distribusinya."),
        ],
    )


def test_the_next_turn_restores_the_data_with_the_calculation_beside_it():
    restored = _latest_thread_result(_session_030_t1())

    assert restored is not None
    assert restored["columns"] == ["order_id", "revenue"]
    assert restored["rows"] == ORDER_ROWS
    assert restored["sql"] == ORDERS_SQL
    assert [table["columns"] for table in restored["derived"]] == [
        ["from", "to", "revenue_pct_change"],
    ]
    assert "calculated" not in restored


def test_an_answer_with_only_a_calculation_still_restores_it():
    restored = _latest_thread_result(_session_030_t1(with_orders=False))

    assert restored is not None
    assert restored["columns"] == ["from", "to", "revenue_pct_change"]
    assert "calculated" not in restored and "derived" not in restored


@pytest.mark.asyncio
async def test_a_chart_of_the_orders_draws_the_order_query(monkeypatch):
    """session-030.t3: the distribution of order values charts the order list."""
    drawn: dict = {}

    async def native_chart(plan, arguments, *, sql, title, context, tool_call_id):
        drawn.update(plan=plan, sql=sql)
        raise chart_module._Refused("captured")

    monkeypatch.setattr(chart_module, "_native_chart", native_chart)
    context = SimpleNamespace(last_result=_latest_thread_result(_session_030_t1()),
                              evidence_tables={}, steps=[])
    outcome = await data_to_chart_tool.run(ToolInvocation(
        "t3", "data_to_chart",
        {"intent": "distribusi nilai pesanan", "visual": "bar",
         "x": "order_id", "y": ["revenue"]},
    ), context)

    assert outcome.error == "captured"
    assert drawn["sql"] == ORDERS_SQL


def test_a_follow_up_about_the_calculation_still_reaches_it():
    """'Chart that change' names the calculated column: the kept calculation is used."""
    restored = _latest_thread_result(_session_030_t1())

    table, evidence_id = chart_module._holding(["revenue_pct_change"], restored, {})
    assert table is not None and table["columns"] == ["from", "to", "revenue_pct_change"]
    assert evidence_id == ""
    table, _ = chart_module._holding(["order_id", "revenue"], restored, {})
    assert table is restored
    table, _ = chart_module._holding([], restored, {})
    assert table is restored


SCENARIO = {"columns": ["shipping_city", "revenue", "revenue_scenario", "revenue_change"],
            "rows": [["Jakarta", "30496000.0000", "60992000.0000", "30496000.0000"]]}


@pytest.mark.asyncio
async def test_a_calculation_of_this_turn_is_found_by_its_columns():
    orders = {"columns": ["order_id", "revenue"], "rows": ORDER_ROWS, "sql": ORDERS_SQL}
    context = SimpleNamespace(last_result=orders, steps=[],
                              evidence_tables={"evidence_1": orders, "evidence_2": SCENARIO})
    outcome = await data_to_chart_tool.run(ToolInvocation(
        "c", "data_to_chart",
        {"intent": "the scenario", "visual": "kpi", "y": ["revenue_scenario"]},
    ), context)

    assert outcome.ok, outcome.error
    assert outcome.component is not None
    assert outcome.component["props"]["items"][0]["evidence_id"] == "evidence_2"
    assert outcome.component["props"]["items"][0]["value"] == 60992000.0


@pytest.mark.asyncio
async def test_a_column_no_result_holds_is_a_recoverable_error():
    """session-030.t3 ended the run with an error naming internal columns."""
    context = SimpleNamespace(
        last_result={"columns": ["from", "to", "revenue_pct_change"],
                     "rows": [["1008", "1013", "-60.0400%"]]},
        evidence_tables={}, steps=[],
    )
    outcome = await data_to_chart_tool.run(ToolInvocation(
        "t3", "data_to_chart",
        {"intent": "distribusi", "visual": "bar", "x": "order_id", "y": ["revenue"]},
    ), context)

    assert not outcome.ok
    assert outcome.recoverable
    assert "order_id" not in (outcome.safe_detail or "")
