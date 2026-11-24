"""A chart follow-up finds the previous turn's result, or queries first.

Benchmark ``session:chart_edit`` / ``session:follow_up`` (8 errors): "Show that
as a pie chart" ended the turn with "There is no recent data to chart in this
conversation" although the result was on screen one turn earlier.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.agents.tools.data_to_chart import DataToChartTool
from app.modules.assistant.service import AssistantLoop, LoopContext, _latest_thread_result
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import EvalTool

PAYMENTS = {
    "title": "Revenue by payment method",
    "columns": ["payment_method", "revenue"],
    "rows": [["Credit Card", "62997000.00"], ["Bank Transfer", "61497000.00"]],
}


def _previous_turn() -> AssistantThread:
    thread = AssistantThread(thread_id="t-chart", user_name="alice", title="Eval")
    thread.messages = [
        AssistantMessage(message_id="u1", role="user", content="Revenue by payment method."),
        AssistantMessage(message_id="a1", role="assistant", content="Credit Card leads.", steps=[
            {"kind": "tool", "name": "semantic_query", "tool_call_id": "q1", "status": "done",
             "preview": "SELECT payment_method, SUM(total_amount) AS revenue FROM orders "
                        "GROUP BY payment_method"},
            {"kind": "table", "tool_call_id": "q1", **PAYMENTS},
        ]),
    ]
    return thread


def test_the_previous_turns_table_and_statement_are_restored():
    restored = _latest_thread_result(_previous_turn())
    assert restored["columns"] == PAYMENTS["columns"]
    assert restored["sql"].startswith("SELECT payment_method")


async def test_an_earlier_evidence_id_charts_the_restored_result():
    context = SimpleNamespace(last_result=_latest_thread_result(_previous_turn()),
                              evidence_tables={})
    outcome = await DataToChartTool().run(
        ToolInvocation("c1", "data_to_chart", {
            "intent": "share of revenue", "evidence_id": "evidence_1", "visual": "kpi",
            "y": ["revenue"],
        }),
        context,
    )
    assert outcome.ok, outcome.error
    assert outcome.component["name"] == "KpiTiles"


async def test_no_data_is_recoverable_so_the_agent_queries_first():
    outcome = await DataToChartTool().run(
        ToolInvocation("c1", "data_to_chart", {"intent": "pie of revenue"}),
        SimpleNamespace(last_result=None, evidence_tables={}),
    )
    assert not outcome.ok and outcome.recoverable
    assert outcome.error_class == "NO_DATA_RESULT"


async def test_the_turn_queries_then_answers_instead_of_failing():
    chart = EvalTool("data_to_chart", parameters={"type": "object", "properties": {
        "intent": {"type": "string"}}}, outcomes=[
        ToolOutcome(ok=False, summary="", error="There is no recent data to chart.",
                    error_class="NO_DATA_RESULT", recoverable=True),
    ])
    query = EvalTool("query_execute", table=PAYMENTS)
    registry = ToolRegistry()
    registry.register(chart)
    registry.register(query)
    provider = ScriptedProvider(script=[
        tool_call_frame("c1", name="data_to_chart", arguments={"intent": "pie"}),
        tool_call_frame("q1", sql="SELECT payment_method, SUM(total_amount) FROM orders"),
        text_frame("Credit Card has the largest share."),
    ])
    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="t-chart-2", user_name="alice", title="Eval"),
            user_content="Show that as a pie chart.",
            context=LoopContext(user_name="alice"),
            resolve_consent=lambda *_: None,
        )
    ]
    output = "".join(frames)
    assert "tool_failed" not in output
    assert len(query.runs) == 1
    assert "largest share" in output
