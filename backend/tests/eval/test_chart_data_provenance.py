"""A chart turn binds a native chart to the governed query, never to rows."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.modules.agents.tools.data_to_chart import DataToChartTool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.viz import studio_drafts
from app.modules.viz.spec import ChartIntent
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval import draft_charts


async def test_chart_turn_emits_a_signed_native_chart_and_finishes(monkeypatch) -> None:
    draft_charts.install(monkeypatch, ["x", "y"], measures={"x", "y"})
    chart_tool = DataToChartTool()
    chart_tool._model_intent = AsyncMock(
        return_value=ChartIntent(chart_type="scatter", x="x", y="y")
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
    context.user = {"username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
                    "session_id": "s1", "security_context_version": 1}
    context.thread_id = "chart-data"
    context.last_result = {
        "title": "Measurements",
        "columns": ["x", "y"],
        "sql": "SELECT x, y FROM sales.points",
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

    component_frames = [frame for frame in frames if frame.startswith("event: component\n")]
    assert len(component_frames) == 1
    payload = json.loads(component_frames[0].split("data: ", 1)[1].split("\n", 1)[0])
    assert payload["component"] == "DatavizChart"
    props = payload["props"]
    assert props["chart"]["visual"] == "scatter"
    # No rows and no statement travel with the chart; the ref names only the
    # caller's own record and opens for no one else.
    assert "SELECT" not in json.dumps(payload) and "rows" not in props["chart"]
    locator = studio_drafts.verify(props["draft_ref"], "alice")
    assert locator.thread_id == "chart-data" and locator.content_id == payload["content_id"]
    step = next(step for step in context.steps if step.get("kind") == "component")
    assert step["source"]["statement"] == "SELECT x, y FROM sales.points"
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")
