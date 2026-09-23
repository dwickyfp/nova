"""A chart turn must render the verified result, not the model's data preview."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.modules.agents.tools.data_to_chart import DataToChartTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


async def test_chart_turn_keeps_all_verified_rows_and_finishes() -> None:
    chart_tool = DataToChartTool()
    chart_tool._model_spec = AsyncMock(
        return_value={
            "mark": "point",
            "data": {"values": [{"x": "model-preview", "y": -1}]},
            "encoding": {
                "x": {"field": "x", "type": "quantitative"},
                "y": {"field": "y", "type": "quantitative"},
            },
        }
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
    spec = json.loads(payload["chart_spec"])
    assert spec["mark"] == "point"
    assert len(spec["data"]["values"]) == 35
    assert spec["data"]["values"][0] == {"x": 0, "y": 0}
    assert spec["data"]["values"][-1] == {"x": 34, "y": 68}
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
