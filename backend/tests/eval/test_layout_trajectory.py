"""Page answers stream from the final answer in one provider call, checked per statement."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.eval import draft_charts
from tests.eval.test_ui_autocompose_trajectory import TableQuery

MONTHLY = {
    "columns": ["month", "revenue", "orders"],
    "rows": [["2025-06-01", "145886000.00", 10], ["2025-07-01", "29298000.00", 5]],
}
PAGE_PLAN = {"intent": "raw_sql_query", "tools": ["query_execute"],
             "required_tools": ["query_execute"], "skills": [], "ml_task": None,
             "presentation": "interactive"}
PAGE = """```openui
root = Page([header, scenario, kpis, trend, summary])
header = Header("Sales overview", "June and July 2025, with a revenue what-if.")
$uplift = 10
scenario = Controls("What-if scenario", [Slider("Revenue change", $uplift, -30, 50, 5, "percent")])
jun = @Value("evidence_1", "revenue", "value", {month: "2025-06"})
jul = @Value("evidence_1", "revenue", "value", {month: "2025-07"})
kpis = KpiTiles([Metric("July revenue", jul, "number", (jul - jun) / jun, "vs. June"), Metric("Scenario", jul * (1 + $uplift / 100), "number", $uplift / 100, "Scenario")])
trend = Section("Trend", [Chart("evidence_1", "line", "month", "revenue", "Revenue by month")])
summary = Text("July revenue was 29,298,000, below June's 145,886,000.")
```"""  # noqa: E501 - one statement per line is the language


def _payloads(frames: list[str], event: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
            for frame in frames if frame.startswith(f"event: {event}\n")]


async def _run(script: list[dict[str, Any]], *, plan: dict[str, Any] | None = None,
               intelligent_ui: bool = True, table: dict[str, Any] | None = None):
    registry = ToolRegistry()
    if table is not None:
        registry.register(TableQuery(table))
    provider = ScriptedProvider(script=script, turn_plan=plan or PAGE_PLAN)
    context = LoopContext(user_name="alice", intelligent_ui=intelligent_ui,
                          user=dict(draft_charts.USER), thread_id="page-eval")
    frames = [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=AssistantThread(thread_id="page-eval", user_name="alice", title="Eval"),
        user_content="Give me a sales overview for June and July 2025 with a what-if",
        context=context, resolve_consent=allow,
    )]
    return frames, context, provider


async def test_an_overview_is_a_checked_page_with_a_native_chart(monkeypatch):
    draft_charts.install(monkeypatch, MONTHLY["columns"], measures={"revenue", "orders"},
                         temporal={"month"})
    frames, context, provider = await _run(
        [tool_call_frame("q-1", arguments={"sql": "SELECT month, revenue, orders FROM s"}),
         text_frame(PAGE)],
        table=MONTHLY,
    )
    [opened] = _payloads(frames, "ui")
    assert opened["payload"]["catalog_version"] == "nova-layout-1"
    deltas = [item["text"] for item in _payloads(frames, "ui_delta")]
    names = [text.split(" =")[0] for text in deltas]
    assert names == ["root", "header", "$uplift", "scenario", "jun", "jul", "kpis", "trend",
                     "summary"]
    program = "".join(deltas)
    assert "jun = 145886000\n" in program and "@Value" not in program
    assert 'DatavizChart("Revenue by month"' in program and "Chart(\"evidence_1\"" not in program
    # The page is the answer: no tool chart or autocomposed view repeats it, and no
    # second model call composes it.
    assert not _payloads(frames, "component")
    assert provider.calls == 2
    [step] = [step for step in context.steps if step.get("kind") == "ui"]
    assert step["program"] + "\n" == program
    assert step["charts"][0]["draft_ref"].startswith("dr1.")
    assert step["charts"][0]["source"]["tool_call_id"]
    assert step["provenance"]["evidence_ids"] == ["evidence_1"]
    assert context.quality_facts["ui_validity"]["valid"] is True
    assert context.quality_facts["ui_evidence"] == {"bound": True}
    # The written answer names the page; the page text is in the fallback for copying.
    assert step["fallback"].startswith("**Sales overview**")


async def test_a_single_number_stays_a_sentence():
    frames, context, provider = await _run(
        [tool_call_frame("q-1"), text_frame("Total revenue was 175,184,000.")],
        plan={**PAGE_PLAN, "presentation": "text"},
        table={"columns": ["total_revenue"], "rows": [["175184000.00"]]},
    )
    assert not _payloads(frames, "ui")
    assert "<PAGE_LAYOUT>" in context.ui_prompt
    assert "one number never becomes a page" in context.ui_prompt
    text = "".join(item["text"] for item in _payloads(frames, "text_delta"))
    assert "175,184,000" in text and provider.calls == 2


async def test_without_the_page_language_no_layout_is_offered():
    frames, context, _ = await _run([text_frame("Hello.")], intelligent_ui=False,
                                    plan={**PAGE_PLAN, "presentation": "text", "tools": [],
                                          "required_tools": []})
    assert not context.ui_prompt and not _payloads(frames, "ui")


async def test_an_invalid_statement_is_left_out_and_the_page_still_shows():
    page = ("```openui\nroot = Page([h, typed, t])\nh = Header(\"Overview\")\n"
            "typed = KpiTiles([Metric(\"Typed\", 999)])\nt = Text(\"Short note.\")\n```")
    frames, context, _ = await _run(
        [text_frame(page)],
        plan={**PAGE_PLAN, "tools": [], "required_tools": [], "intent": "direct_answer"},
    )
    names = [item["text"].split(" =")[0] for item in _payloads(frames, "ui_delta")]
    assert names == ["root", "h", "t"]
    [step] = [step for step in context.steps if step.get("kind") == "ui"]
    assert [error["statement"] for error in step["provenance"]["errors"]] == ["typed"]
    assert context.quality_facts["ui_validity"] == {"valid": True, "statements": 3, "errors": 1}


@pytest.mark.parametrize("invalid", ['root = Col([Unknown("x")])', 'root = Header("x"'])
async def test_a_page_with_nothing_valid_keeps_the_written_answer(invalid):
    frames, context, provider = await _run(
        [text_frame("Revenue is steady.\n```openui\n" + invalid + "\n```")],
        plan={**PAGE_PLAN, "tools": [], "required_tools": [], "intent": "direct_answer",
              "presentation": "text"},
    )
    assert not _payloads(frames, "ui") and provider.calls == 1
    text = "".join(item["text"] for item in _payloads(frames, "text_delta"))
    assert text == "Revenue is steady."
    assert any(step.get("ui_fallback") == "validation_failed" for step in context.steps)


async def test_a_page_written_before_a_tool_call_is_retracted():
    early = "```openui\nroot = Page([h])\nh = Header(\"Too early\")\n```"
    frames, _, _ = await _run(
        [{**tool_call_frame("q-1"), "content": early},
         text_frame('```openui\nroot = Page([h])\nh = Header("Revenue")\n```')],
        plan={**PAGE_PLAN, "presentation": "text"},
        table=MONTHLY,
    )
    opened = _payloads(frames, "ui")
    deltas = _payloads(frames, "ui_delta")
    assert len(opened) == 2 and opened[0]["content_id"] != opened[1]["content_id"]
    retracted = [item for item in deltas if item.get("replace")]
    assert [item["content_id"] for item in retracted] == [opened[0]["content_id"]]
    assert deltas[-1]["text"] == 'h = Header("Revenue")\n'
