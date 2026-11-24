"""F9: a page answer is remembered with its figures, and shown again from storage.

Re-bench session-012.t3: "Kesimpulannya apa?" after a page answer was answered
"the figures are not available here": a page has little text around it, and
the history replayed none of the page. session-029.t2: "Just give me a table
instead" after a map wrote ``Table`` over a result the new turn did not hold.
"""

from __future__ import annotations

from app.modules.agents.tools.data_to_chart import _explicit_plan
from app.modules.assistant import ui_layout as ul
from app.modules.assistant.service import (
    LoopContext,
    _history_artifact_context,
    _previous_page_result,
)
from app.modules.assistant.state import AssistantMessage, AssistantThread
from tests.unit.test_ui_layout import ANSWER, _stream

USER = {"username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
        "session_id": "s1", "security_context_version": 1}
STAMP = {"principal": "alice", "active_role": "ANALYST", "security_context_version": 1,
         "session_id": "s1"}
ORDERS = {"title": "Orders by shipping city", "columns": ["shipping_city", "order_count"],
          "rows": [["Semarang", 1], ["Bandung", 2]], "source": "previous_turn"}


async def test_a_page_keeps_its_verified_figures_and_leaves_scenarios_out():
    stream, _ = await _stream(ANSWER, 10_000)
    result = stream.result()

    assert result.figures == ["Baseline revenue: 175184000", "July vs June: 29298000 (July 2025)"]
    text = ul.fallback_text(result)
    assert text.startswith("**Sales overview**")
    assert "- Baseline revenue: 175184000\n- July vs June: 29298000 (July 2025)" in text
    assert "Scenario revenue" not in text and "Typed" not in text


async def test_percent_and_currency_figures_read_as_the_page_shows_them():
    stream = ul.LayoutStream(ul.LayoutContext(tables={"evidence_1": {
        "columns": ["share", "revenue"], "rows": [[12.5, 2500000]], "sql": "SELECT 1"}}))
    await stream.feed('```openui\nroot = Page([k])\nk = KpiTiles([Metric("Share", '
                      '@Value("evidence_1", "share", "value"), "percent"), Metric("Revenue", '
                      '@Value("evidence_1", "revenue", "value"), "currency", null, null, "IDR")])'
                      "\n```")
    await stream.finish()

    assert stream.result().figures == ["Share: 12.5%", "Revenue: 2500000 IDR"]


def test_history_replays_a_page_only_in_the_callers_context():
    steps = [{"kind": "ui", "program": "root = Page([k])",
              "fallback": "**June sales**\n\nRevenue fell.\n\n- Revenue: 29298000"}]

    assert "page shown: **June sales** Revenue fell. - Revenue: 29298000" in (
        _history_artifact_context(steps, pages=True))
    assert _history_artifact_context(steps, pages=False) == ""


def _thread(*stamps: dict | None) -> AssistantThread:
    thread = AssistantThread(thread_id="t", user_name="alice", title="t")
    thread.messages = [AssistantMessage(message_id=f"m{i}", role="assistant", content="",
                                        security_context=stamp)
                       for i, stamp in enumerate(stamps)]
    return thread


def test_the_previous_result_is_offered_to_a_page_in_the_same_context():
    context = LoopContext(user_name="alice", user=dict(USER))
    context.last_result = dict(ORDERS)

    assert _previous_page_result(_thread(STAMP, STAMP), context) == ORDERS


def test_a_role_switch_keeps_the_previous_rows_off_the_page():
    context = LoopContext(user_name="alice", user=dict(USER))
    context.last_result = dict(ORDERS)
    other = {**STAMP, "active_role": "MARKETING"}

    assert _previous_page_result(_thread(other, STAMP), context) is None
    assert _previous_page_result(_thread(None), context) is None
    context.last_result = {**ORDERS, "source": "query"}  # this turn's own result
    assert _previous_page_result(_thread(STAMP), context) is None


def test_the_composer_names_the_previous_result():
    prompt = ul.composer_prompt(enabled=True, presentation="direct", previous=ORDERS)

    assert ('The previous answer\'s result is available as "previous_result" (Orders by '
            "shipping city: columns shipping_city, order_count; 2 rows)") in prompt
    assert '"previous_result"' not in ul.composer_prompt(enabled=True, presentation="direct")


def test_a_donut_stays_a_donut():
    """data_to_chart.py:476 read "donut" as a pie; the pie is now drawn with its hole."""
    plan = _explicit_plan({"visual": "pie", "x": "city", "y": ["revenue"],
                           "intent": "Tampilkan sebagai donut chart"})
    plain = _explicit_plan({"visual": "pie", "x": "city", "y": ["revenue"],
                            "intent": "share by city"})

    assert plan is not None and plan["donut"] is True
    assert plain is not None and "donut" not in plain
