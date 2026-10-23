"""Verified results no other visual shows get a map or KPI without a model call."""

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
        context.last_result = {"title": "", **self.table}
        return ToolOutcome(ok=True, summary=f"{len(self.table['rows'])} rows",
                           table=dict(self.table))


def _payloads(frames: list[str], event: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
            for frame in frames if frame.startswith(f"event: {event}\n")]


async def _run(script: list[dict[str, Any]], registry: ToolRegistry, *, intelligent_ui: bool,
               plan: dict[str, Any]) -> tuple[list[str], LoopContext, ScriptedProvider]:
    provider = ScriptedProvider(script=script, turn_plan=plan)
    context = LoopContext(user_name="alice", intelligent_ui=intelligent_ui)
    frames = [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=AssistantThread(thread_id="auto-ui", user_name="alice", title="Eval"),
        user_content="Chart revenue by salesperson", context=context, resolve_consent=allow,
    )]
    return frames, context, provider


def _chart_registry() -> ToolRegistry:
    chart = DataToChartTool()
    chart._model_spec = AsyncMock(return_value={  # type: ignore[method-assign]
        "mark": "bar",
        "data": {"values": []},
        "encoding": {
            "y": {"field": "salesperson", "type": "nominal", "sort": "-x"},
            "x": {"field": "revenue", "type": "quantitative"},
        },
    })
    registry = ToolRegistry()
    registry.register(TableQuery(SALES))
    registry.register(chart)
    return registry


CHART_PLAN = {"intent": "chart", "tools": ["query_execute", "data_to_chart"],
              "required_tools": ["query_execute", "data_to_chart"], "skills": [],
              "ml_task": None, "presentation": "text"}
CHART_SCRIPT = [
    tool_call_frame("q-1", name="query_execute", arguments={"sql": "SELECT 1"}),
    tool_call_frame("c-1", name="data_to_chart", arguments={"intent": "revenue"}),
    text_frame("Revenue by salesperson is shown below."),
]


async def test_a_charted_answer_gets_no_extra_visual_or_provider_call() -> None:
    frames, context, provider = await _run(
        [dict(item) for item in CHART_SCRIPT], _chart_registry(),
        intelligent_ui=True, plan=CHART_PLAN,
    )
    assert not _payloads(frames, "ui") and len(_payloads(frames, "chart")) == 1
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
    assert len(_payloads(frames, "ui")) == 1
    specialist, _, _ = await _map_run(intelligent_ui=False)
    assert not _payloads(specialist, "ui")
    assert len(_payloads(frames, "table")) == 1


async def test_a_charted_located_result_still_gets_its_map() -> None:
    registry = _chart_registry()
    registry.register(TableQuery(OUTLETS))
    frames, _, _ = await _run(
        [dict(item) for item in CHART_SCRIPT], registry, intelligent_ui=True, plan=CHART_PLAN,
    )
    ui = _payloads(frames, "ui")
    assert len(_payloads(frames, "chart")) == 1
    assert len(ui) == 1 and ui[0]["payload"]["provenance"]["auto"] == "map"


async def test_a_rejected_program_on_a_located_result_still_shows_its_map() -> None:
    invalid = 'Two outlets are listed.\n```openui\nroot = Col([Unknown("x")])\n```'
    registry = ToolRegistry()
    registry.register(TableQuery(OUTLETS))
    frames, context, _ = await _run(
        [tool_call_frame("q-1", name="query_execute", arguments={"sql": "SELECT 1"}),
         text_frame(invalid), text_frame(invalid)],
        registry, intelligent_ui=True, plan=MAP_PLAN,
    )
    ui = _payloads(frames, "ui")
    assert len(ui) == 1 and ui[0]["payload"]["provenance"]["auto"] == "map"
    assert any(step.get("ui_fallback") == "validation_failed" for step in context.steps)
    assert "Only text is available" not in "".join(frames)


async def test_located_result_gets_a_governed_map() -> None:
    frames, _, provider = await _map_run()
    ui = _payloads(frames, "ui")
    assert len(ui) == 1 and ui[0]["payload"]["provenance"]["auto"] == "map"
    assert ui[0]["payload"]["provenance"]["tool_calls"] == {"evidence_1": "q-1"}
    program = "".join(item["text"] for item in _payloads(frames, "ui_delta"))
    assert 'GeoMap(@Evidence("evidence_1"), "longitude", "latitude"' in program
    assert len(_payloads(frames, "table")) == 1
    assert provider.calls == 2
