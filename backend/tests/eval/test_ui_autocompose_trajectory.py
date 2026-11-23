"""Verified results no other visual shows get a map, chart or KPI without a model call."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from app.modules.agents.tools.data_to_chart import DataToChartTool
from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolInvocation, ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.eval import draft_charts

SALES = {
    "columns": ["salesperson", "revenue"],
    "rows": [["Budi", "1200.00"], ["Rizky", "3200.00"], ["Sari", "800.00"]],
}
OUTLETS = {
    "columns": ["outlet_name", "latitude", "longitude", "district"],
    "rows": [["Toko A", -6.24, 106.79, "Cilandak"], ["Toko B", -6.25, 106.78, "Kebayoran"]],
}


class TableQuery:
    """A read-only query at the registry seam that returns one verified table."""

    name = "query_execute"
    description = "run a read-only statement"
    parameters = {"type": "object", "properties": {"sql": {"type": "string"}}}
    classification: ToolClassification = "read_only"
    requires_consent = False

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    def preview(self, invocation: ToolInvocation) -> str:
        return str(invocation.arguments.get("sql", "SELECT 1"))

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        context.last_result = {"title": "", **self.table,
                               "sql": str(invocation.arguments.get("sql") or "")}
        return ToolOutcome(ok=True, summary=f"{len(self.table['rows'])} rows",
                           table=dict(self.table))


def _payloads(frames: list[str], event: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
            for frame in frames if frame.startswith(f"event: {event}\n")]


async def _run(script: list[dict[str, Any]], registry: ToolRegistry, *, intelligent_ui: bool,
               plan: dict[str, Any]) -> tuple[list[str], LoopContext, ScriptedProvider]:
    provider = ScriptedProvider(script=script, turn_plan=plan)
    context = LoopContext(user_name="alice", intelligent_ui=intelligent_ui,
                          user=dict(draft_charts.USER), thread_id="auto-ui")
    frames = [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=AssistantThread(thread_id="auto-ui", user_name="alice", title="Eval"),
        user_content="Chart revenue by salesperson", context=context, resolve_consent=allow,
    )]
    return frames, context, provider


def _chart_registry() -> ToolRegistry:
    chart = DataToChartTool()
    chart._model_intent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    registry = ToolRegistry()
    registry.register(TableQuery(SALES))
    registry.register(chart)
    return registry


CHART_PLAN = {"intent": "chart", "tools": ["query_execute", "data_to_chart"],
              "required_tools": ["query_execute", "data_to_chart"], "skills": [],
              "ml_task": None, "presentation": "text"}
CHART_SCRIPT = [
    tool_call_frame("q-1", name="query_execute",
                    arguments={"sql": "SELECT salesperson, revenue FROM sales.people"}),
    tool_call_frame("c-1", name="data_to_chart", arguments={"intent": "revenue"}),
    text_frame("Revenue by salesperson is shown below."),
]


def _components(frames: list[str], name: str) -> list[dict[str, Any]]:
    return [item for item in _payloads(frames, "component") if item["component"] == name]


async def test_a_charted_answer_gets_no_extra_visual_or_provider_call(monkeypatch) -> None:
    draft_charts.install(monkeypatch, ["salesperson", "revenue"], measures={"revenue"})
    frames, context, provider = await _run(
        [dict(item) for item in CHART_SCRIPT], _chart_registry(),
        intelligent_ui=True, plan=CHART_PLAN,
    )
    assert not _payloads(frames, "ui") and len(_payloads(frames, "component")) == 1
    chart = _components(frames, "DatavizChart")[0]["props"]
    assert chart["chart"]["visual"] == "bar" and chart["draft_ref"].startswith("dr1.")
    assert "OPTIONAL_UI_CATALOG" in context.ui_prompt
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
    _, _, plain_provider = await _run(
        [dict(item) for item in CHART_SCRIPT], _chart_registry(),
        intelligent_ui=False, plan=CHART_PLAN,
    )
    assert plain_provider.calls == provider.calls


MAP_PLAN = {"intent": "raw_sql_query", "tools": ["query_execute"],
            "required_tools": ["query_execute"], "skills": [], "ml_task": None,
            "presentation": "text"}


def _map_run(intelligent_ui: bool = True):
    registry = ToolRegistry()
    registry.register(TableQuery(OUTLETS))
    return _run(
        [tool_call_frame("q-1", name="query_execute", arguments={"sql": "SELECT 1"}),
         text_frame("Two outlets are listed.")],
        registry, intelligent_ui=intelligent_ui, plan=MAP_PLAN,
    )


async def test_participant_gate_preserves_the_map_for_user_answers() -> None:
    frames, _, _ = await _map_run()
    assert len(_components(frames, "GeoMap")) == 1
    specialist, _, _ = await _map_run(intelligent_ui=False)
    assert not _payloads(specialist, "component") and not _payloads(specialist, "ui")
    assert len(_payloads(frames, "table")) == 1


async def test_a_charted_located_result_still_gets_its_map(monkeypatch) -> None:
    draft_charts.install(monkeypatch, OUTLETS["columns"],
                         measures={"latitude", "longitude"})
    registry = _chart_registry()
    registry.register(TableQuery(OUTLETS))
    frames, _, _ = await _run(
        [dict(item) for item in CHART_SCRIPT], registry, intelligent_ui=True, plan=CHART_PLAN,
    )
    assert len(_components(frames, "DatavizChart")) == 1
    assert len(_components(frames, "GeoMap")) == 1


async def test_a_rejected_program_on_a_located_result_still_shows_its_map() -> None:
    invalid = 'Two outlets are listed.\n```openui\nroot = Col([Unknown("x")])\n```'
    registry = ToolRegistry()
    registry.register(TableQuery(OUTLETS))
    frames, context, _ = await _run(
        [tool_call_frame("q-1", name="query_execute", arguments={"sql": "SELECT 1"}),
         text_frame(invalid), text_frame(invalid)],
        registry, intelligent_ui=True, plan=MAP_PLAN,
    )
    assert len(_components(frames, "GeoMap")) == 1 and not _payloads(frames, "ui")
    assert any(step.get("ui_fallback") == "validation_failed" for step in context.steps)
    assert "Only text is available" not in "".join(frames)


async def test_located_result_gets_a_governed_map() -> None:
    frames, _, provider = await _map_run()
    maps = _components(frames, "GeoMap")
    assert len(maps) == 1 and not _payloads(frames, "ui")
    props = maps[0]["props"]
    assert props["evidence_id"] == "evidence_1"
    assert (props["longitude"], props["latitude"], props["label"]) == (
        "longitude", "latitude", "outlet_name")
    assert [row["outlet_name"] for row in props["rows"]] == ["Toko A", "Toko B"]
    assert len(_payloads(frames, "table")) == 1
    assert provider.calls == 2


REGION = {
    "columns": ["region", "revenue_total"],
    "rows": [["Surabaya", "803480000.00"], ["Jakarta", "979755000.00"],
             ["Medan", "507710000.00"]],
}


def _events(frames: list[str]) -> set[str]:
    return {frame.split("\n", 1)[0].removeprefix("event: ")
            for frame in frames if frame.startswith("event: ")}


async def test_a_plain_data_answer_gets_a_ranking_on_the_same_stream(monkeypatch) -> None:
    draft_charts.install(monkeypatch, REGION["columns"], measures={"revenue_total"})
    registry = ToolRegistry()
    registry.register(TableQuery(REGION))
    script = [tool_call_frame("q-1", name="query_execute",
                              arguments={"sql": "SELECT region, revenue_total FROM sales.r"}),
              text_frame("Jakarta leads revenue.")]
    frames, context, provider = await _run(
        [dict(item) for item in script], registry, intelligent_ui=True, plan=MAP_PLAN,
    )
    charts = _components(frames, "DatavizChart")
    assert len(charts) == 1 and not _payloads(frames, "ui")
    chart = charts[0]["props"]["chart"]
    assert chart["visual"] == "bar"
    assert chart["sort"][0]["direction"] == "desc"
    step = next(step for step in context.steps if step.get("kind") == "component")
    # The query that produced the rows is the chart's record, not a copy of it.
    query = next(step for step in context.steps if step.get("kind") == "tool")
    assert step["source"]["tool_call_id"] == query["tool_call_id"]
    assert "statement" not in step["source"]
    # No provider call is added, and the view travels on the frames a map already uses.
    assert provider.calls == 2
    map_frames, _, _ = await _map_run()
    # Besides a map's frames, a chart draft is announced as an artifact with
    # at most one save suggestion (it is never saved by the turn).
    artifacts = {"artifact_draft", "save_suggested"}
    assert "artifact_draft" in _events(frames)
    assert _events(frames) - artifacts <= _events(map_frames)
    plain, _, _ = await _run(
        [dict(item) for item in script], registry, intelligent_ui=False, plan=MAP_PLAN,
    )
    assert not _payloads(plain, "component")
