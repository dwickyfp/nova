"""A chart turn must render the verified result, not the model's data preview."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.modules.agents.tools.data_to_chart import DataToChartTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.viz.spec import NotionalSpec
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


async def test_chart_turn_keeps_all_verified_rows_and_finishes() -> None:
    chart_tool = DataToChartTool()
    chart_tool._model_notional = AsyncMock(
        return_value=NotionalSpec(chart_type="scatter", x="x", y="y")
    )
    registry = ToolRegistry()
    registry.register(chart_tool)
    provider = ScriptedProvider(
        script=[
            tool_call_frame("chart-1", name="data_to_chart", arguments={"intent": "scatter"}),
            text_frame("Here is the chart."),
        ]
    )
    context = LoopContext(user_name="alice")
    context.last_result = {
        "title": "Measurements",
        "columns": ["x", "y"],
        "rows": [[i, i * 2] for i in range(35)],
    }

    async def consent(_invocation, _classification):
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="chart-data", user_name="alice", title="Eval"),
            user_content="Visualize that result as a scatter chart.",
            context=context,
            resolve_consent=consent,
        )
    ]

    chart_frames = [frame for frame in frames if frame.startswith("event: chart\n")]
    assert len(chart_frames) == 1
    payload = json.loads(chart_frames[0].split("data: ", 1)[1].split("\n", 1)[0])
    # The viz core keeps every verified row: one mark per row, never the
    # twenty-row preview the model saw.
    assert payload["viz_spec"]["marks"]["type"] == "circle"
    assert payload["presmodel"]["row_count"] == 35
    assert payload["presmodel"]["rows"][0] == [0, 0]
    assert payload["presmodel"]["rows"][-1] == [34, 68]
    # The Vega mirror still ships for replay paths that speak it.
    assert json.loads(payload["chart_spec"])["mark"] == "circle"
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
