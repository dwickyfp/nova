"""Follow-ups to a page answer, replayed through the loop (F9).

session-012.t3: "Kesimpulannya apa?" after a page; the model must receive the
page's figures. session-029.t2: "Just give me a table instead" after a map;
the page shows the previous result as a table from the stored rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantMessage, AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame
from tests.eval import draft_charts

STAMP = {"principal": draft_charts.USER["username"],
         "active_role": draft_charts.USER["active_role"],
         "security_context_version": draft_charts.USER["security_context_version"],
         "session_id": draft_charts.USER["session_id"]}
#: The stored page of session-012.t2 (a sales overview with its KPI tiles).
PAGE_STEP = {"kind": "ui", "content_index": 0, "content_id": "c1", "program": "root = Page([k])",
             "fallback": "**Ringkasan penjualan Juni-Juli 2025**\n\nPendapatan Juli turun.\n\n"
                         "- Pendapatan Juli: 29298000 (vs. Juni)\n- Pendapatan Juni: 145886000"}
#: The table session-029.t1 drew its map from.
TABLE_STEP = {"kind": "table", "content_index": 0, "content_id": "c1", "tool_call_id": "q1",
              "title": "Orders by shipping city", "columns": ["shipping_city", "order_count"],
              "rows": [["Semarang", 1], ["Bandung", 2], ["Jakarta", 3]]}
TABLE_PAGE = """```openui
root = Page([orders])
orders = Table("previous_result", ["shipping_city", "order_count"], "Orders by shipping city")
```"""


@dataclass
class _Recording(ScriptedProvider):
    sent: list[list[dict[str, Any]]] = field(default_factory=list)

    async def stream(self, *, messages, tools=None, provider=None):  # type: ignore[override]
        self.sent.append([dict(item) for item in messages])
        async for frame in super().stream(messages=messages, tools=tools, provider=provider):
            yield frame


def _payloads(frames: list[str], event: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
            for frame in frames if frame.startswith(f"event: {event}\n")]


async def _run(question: str, step: dict, reply: str, stamp: dict | None = STAMP):
    thread = AssistantThread(thread_id="page-eval", user_name="alice", title="Eval")
    thread.messages = [
        AssistantMessage(message_id="u1", role="user", content="Ringkas penjualan",
                         security_context=stamp),
        AssistantMessage(message_id="a1", role="assistant", content="", steps=[step],
                         security_context=stamp),
    ]
    provider = _Recording(script=[text_frame(reply)],
                          turn_plan={"intent": "direct_answer", "tools": [],
                                     "required_tools": [], "skills": [], "ml_task": None})
    context = LoopContext(user_name="alice", intelligent_ui=True,
                          user=dict(draft_charts.USER), thread_id="page-eval")
    frames = [frame async for frame in AssistantLoop(
        provider=provider, registry=ToolRegistry()).run(
        thread=thread, user_content=question, context=context, resolve_consent=allow)]
    return frames, provider


async def test_a_follow_up_to_a_page_receives_its_figures():
    _, provider = await _run("Kesimpulannya apa?", PAGE_STEP, "Pendapatan Juli turun.")

    sent = "\n".join(str(item.get("content")) for item in provider.sent[0])
    assert "page shown: **Ringkasan penjualan Juni-Juli 2025**" in sent
    assert "Pendapatan Juli: 29298000 (vs. Juni)" in sent


async def test_a_page_from_another_role_is_not_replayed():
    other = {**STAMP, "active_role": "MARKETING"}
    _, provider = await _run("Kesimpulannya apa?", PAGE_STEP, "Tidak ada.", stamp=other)

    sent = "\n".join(str(item.get("content")) for item in provider.sent[0])
    assert "29298000" not in sent


async def test_a_table_instead_shows_the_previous_rows():
    frames, provider = await _run("Just give me a table instead", TABLE_STEP, TABLE_PAGE)

    program = "".join(item["text"] for item in _payloads(frames, "ui_delta"))
    assert 'ResultTable("Orders by shipping city", ["shipping_city", "order_count"]' in program
    assert '["Jakarta", 3]' in program
    [done] = _payloads(frames, "done")
    assert done["finish_reason"] == "stop"
    sent = "\n".join(str(item.get("content")) for item in provider.sent[0])
    assert 'available as "previous_result" (Orders by shipping city' in sent


async def test_after_a_role_switch_the_previous_rows_are_not_shown():
    other = {**STAMP, "active_role": "MARKETING"}
    frames, _ = await _run("Just give me a table instead", TABLE_STEP, TABLE_PAGE, stamp=other)

    program = "".join(item["text"] for item in _payloads(frames, "ui_delta"))
    assert "Jakarta" not in program and "ResultTable" not in program
