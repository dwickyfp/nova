"""A dashboard build turn publishes BI artifacts and a replayable block."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.modules.agents.tools.dashboard_build import dashboard_build_tool
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from app.modules.superset import artifacts as bi_artifacts
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame


async def test_dashboard_build_creates_artifacts_and_a_dashboard_frame(monkeypatch) -> None:
    saved = [
        {"artifact_id": "chart-1", "title": "Revenue"},
        {"artifact_id": "chart-2", "title": "Trend"},
    ]

    monkeypatch.setattr(
        bi_artifacts, "publish_chart", AsyncMock(side_effect=lambda *a, **k: saved.pop(0))
    )
    monkeypatch.setattr(
        bi_artifacts,
        "publish_dashboard",
        AsyncMock(return_value={"artifact_id": "dash-1", "title": "Overview"}),
    )

    registry = ToolRegistry()
    registry.register(dashboard_build_tool)
    provider = ScriptedProvider(
        script=[
            tool_call_frame(
                "call-1",
                name="dashboard_build",
                arguments={
                    "title": "Overview",
                    "charts": [
                        {"title": "Revenue", "chart_type": "bar", "x": "region", "y": "revenue"},
                        {"title": "Trend", "chart_type": "line", "x": "month", "y": "revenue"},
                    ],
                },
            ),
            text_frame("Your dashboard is ready."),
        ]
    )
    context = LoopContext(user_name="alice")
    context.user = {
        "username": "alice",
        "encrypted_password": "enc",
        "session_id": "s",
        "roles": ["analyst"],
        "active_role": "analyst",
    }
    context.last_result = {
        "title": "Sales",
        "columns": ["region", "month", "revenue"],
        "rows": [["West", "2026-01-01", 100]],
        "sql": "SELECT region, month, revenue FROM sales.summary",
    }

    async def consent(_invocation, _classification):
        return True

    frames = [
        frame
        async for frame in AssistantLoop(provider=provider, registry=registry).run(
            thread=AssistantThread(thread_id="dash", user_name="alice", title="Eval"),
            user_content="Build a dashboard from that result.",
            context=context,
            resolve_consent=consent,
        )
    ]

    dashboard_frames = [frame for frame in frames if frame.startswith("event: dashboard\n")]
    assert len(dashboard_frames) == 1
    payload = json.loads(dashboard_frames[0].split("data: ", 1)[1].split("\n", 1)[0])
    assert payload["payload"]["artifact_id"] == "dash-1"
    assert [chart["artifact_id"] for chart in payload["payload"]["charts"]] == [
        "chart-1",
        "chart-2",
    ]
    assert '"finish_reason":"stop"' in "".join(frames).replace(" ", "")


async def test_dashboard_build_needs_the_turns_verified_data() -> None:
    from app.modules.assistant.tools import ToolInvocation

    context = LoopContext(user_name="alice")
    context.user = {"username": "alice", "encrypted_password": "enc", "session_id": "s"}
    context.last_result = None

    outcome = await dashboard_build_tool.run(
        ToolInvocation("call-1", "dashboard_build", {"title": "D", "charts": [{"title": "C"}]}),
        context,
    )
    assert not outcome.ok
    assert "no recent data" in outcome.error.lower()
