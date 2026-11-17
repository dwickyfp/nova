"""Native Dataviz changes through the real bounded loop.

* "Add a bar chart of revenue by city to this dashboard" runs the native add
  tool only after the user's consent, as the caller, and the stream tells the
  page which dashboard revision was committed.
* A denial changes nothing and emits no change event.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.modules.agents.tools import native_dataviz
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, tool_call_frame
from tests.eval.harness import TurnResult
from tests.unit.test_native_dataviz_assistant_tools import DASHBOARD, FakeDocuments

USER = {
    "username": "alice",
    "active_role": "ANALYST",
    "roles": ["ANALYST"],
    "security_context_version": 3,
    "session_id": "s-alice",
}

PLAN = {
    "intent": "ui_operation",
    "tools": ["native_dashboard_read", "native_dashboard_add_charts"],
    "required_tools": ["native_dashboard_add_charts"],
    "skills": [],
    "ml_task": None,
}
CHART = {"title": "Revenue by city", "visual": "bar", "dimensions": [{"field": "City"}],
         "measures": [{"field": "Revenue", "aggregation": "sum"}]}


async def _run(consent) -> TurnResult:
    registry = ToolRegistry()
    native_dataviz.register(registry)
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"native_dashboard": {"dashboard_id": str(DASHBOARD), "revision": 1}}
    provider = ScriptedProvider(
        [
            tool_call_frame("c1", name="native_dashboard_add_charts",
                            arguments={"charts": [CHART]}),
            text_frame("I added the bar chart Revenue by city to Sales."),
        ],
        turn_plan=PLAN,
    )
    frames = [
        frame
        async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="test",
        ).run(
            thread=AssistantThread("t1", "alice", "Dataviz"),
            user_content="add a bar chart of revenue by city to this dashboard",
            context=context,
            resolve_consent=consent,
        )
    ]
    return TurnResult(frames=frames)


def _answer(result: TurnResult) -> str:
    """The answer as streamed to the person (``TurnResult.text`` is never filled)."""
    return "".join(json.loads(frame.split("data: ", 1)[1]).get("text", "")
                   for frame in result.frames if frame.startswith("event: text_delta\n"))


def _changes(result: TurnResult) -> list[dict]:
    return [json.loads(frame.split("data: ", 1)[1]) for frame in result.frames
            if frame.startswith("event: dashboard_changed")]


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_a_chart_is_added_only_after_consent_and_the_page_is_told(monkeypatch, allowed):
    docs = FakeDocuments()
    monkeypatch.setattr(native_dataviz, "_documents", lambda: docs)
    prompts: list[tuple[str, str, str]] = []

    async def consent(invocation, classification):
        tool = native_dataviz.AddChartsTool()
        prompts.append((invocation.tool_name, classification, tool.preview(invocation)))
        return allowed

    result = await _run(consent)
    assert prompts == [("native_dashboard_add_charts", "session_change",
                        "Add to the dashboard: bar chart 'Revenue by city'")]
    if allowed:
        assert result.finish_reason == "stop"
        assert len(docs.applied) == 1
        (change,) = _changes(result)
        assert (change["dashboard_id"], change["revision"]) == (str(DASHBOARD), 2)
        assert set(change) == {"tool_call_id", "dashboard_id", "revision", "run_id", "sequence"}
        chart = docs.created[0][1]
        assert (chart.visual, chart.encoding.dimensions[0].field_id) == ("bar", "city")
    else:
        assert result.finish_reason == "denied"
        assert not docs.applied and not docs.created and not _changes(result)


@pytest.mark.asyncio
async def test_without_a_caller_nothing_is_read():
    # The tools act only as the loop's caller; there is no other identity to use.
    tool = native_dataviz.ReadDashboardTool()
    outcome = await tool.run(native_dataviz.ToolInvocation("c", tool.name, {}),
                             SimpleNamespace(user=None, dataviz=None))
    assert not outcome.ok and "signed-in" in outcome.error


@pytest.mark.asyncio
async def test_a_data_question_on_a_native_dashboard_is_answered_from_its_dataset(monkeypatch):
    from tests.unit.test_native_dataviz_assistant_tools import DATASET, FakeDatasets

    docs, data = FakeDocuments(), FakeDatasets()
    monkeypatch.setattr(native_dataviz, "_documents", lambda: docs)
    monkeypatch.setattr(native_dataviz, "_datasets", lambda: data)
    registry = ToolRegistry()
    native_dataviz.register(registry)
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"native_dashboard": {"dashboard_id": str(DASHBOARD), "revision": 1}}
    # As the router sets them for the Dataviz assistant on a native dashboard.
    context.capability_aliases = {"semantic_query": "native_dataset_query"}
    context.dashboard_tools = native_dataviz.TOOL_NAMES
    context.settled_capabilities = ("native_dashboard_read",)
    provider = ScriptedProvider(
        [
            tool_call_frame("q1", name="native_dataset_query", arguments={
                "dataset_id": str(DATASET), "dimensions": [{"field": "City"}],
                "measures": [{"field": "Revenue", "aggregation": "sum"}],
            }),
            text_frame("Surabaya leads with revenue 1573810277.5; Malang follows with 12."),
        ],
        turn_plan={
            "intent": "compound_analytics", "tools": [],
            "required_tools": ["semantic_query"], "skills": [], "ml_task": None,
        },
    )

    async def consent(_invocation, _classification):
        raise AssertionError("a read needs no approval")

    result = TurnResult(frames=[
        frame async for frame in AssistantLoop(
            provider=provider, registry=registry, system_prompt="test",
        ).run(
            thread=AssistantThread("t1", "alice", "Dataviz"),
            user_content="which city has the most revenue?",
            context=context, resolve_consent=consent,
        )
    ])
    joined = "".join(result.frames)
    assert result.finish_reason == "stop", joined[-600:]
    assert len(data.requests) == 1 and data.requests[0].revision == 1
    assert "required_capability" not in joined and not _changes(result)


async def _question(monkeypatch, script, required):
    from app.modules.agents.tools.compute_metrics import ComputeMetricsTool
    from tests.unit.test_native_dataviz_assistant_tools import FakeDatasets

    docs, data = FakeDocuments(), FakeDatasets()
    monkeypatch.setattr(native_dataviz, "_documents", lambda: docs)
    monkeypatch.setattr(native_dataviz, "_datasets", lambda: data)
    registry = ToolRegistry()
    native_dataviz.register(registry)
    registry.register(ComputeMetricsTool())
    context = LoopContext("alice", user=USER, thread_id="t1")
    context.dataviz = {"native_dashboard": {"dashboard_id": str(DASHBOARD), "revision": 1}}
    context.capability_aliases = {"semantic_query": "native_dataset_query"}
    context.dashboard_tools = native_dataviz.TOOL_NAMES
    context.settled_capabilities = ("native_dashboard_read",)
    # The Dataviz assistant runs as an agent, like any Studio agent.
    context.agent_scope = {"agent": "Dataviz assistant"}
    provider = ScriptedProvider(script, turn_plan={
        "intent": "semantic_analytics", "tools": list(required), "required_tools": required,
        "skills": [], "ml_task": None,
    })

    async def consent(_invocation, _classification):
        raise AssertionError("a read needs no approval")

    result = TurnResult(frames=[
        frame async for frame in AssistantLoop(
            # As the router runs the Dataviz assistant: iterative, with analysis tools.
            provider=provider, registry=registry, system_prompt="test", iterative=True,
        ).run(
            thread=AssistantThread("t1", "alice", "Dataviz"),
            user_content="which city has the most revenue, and by how much does it lead?",
            context=context, resolve_consent=consent,
        )
    ])
    return result, data


@pytest.mark.asyncio
async def test_a_query_mistake_is_corrected_within_the_turn(monkeypatch):
    # Live run 2026-11-16: one wrong argument ended the whole answer.
    from tests.unit.test_native_dataviz_assistant_tools import DATASET

    query = {"dataset_id": str(DATASET), "dimensions": [{"field": "City"}],
             "measures": [{"field": "Revenue", "aggregation": "sum"}]}
    result, data = await _question(monkeypatch, [
        tool_call_frame("q1", name="native_dataset_query",
                        arguments={**query, "sort": {"field": "profit", "direction": "desc"}}),
        tool_call_frame("q2", name="native_dataset_query", arguments=query),
        text_frame("Surabaya leads with revenue 1573810277.5; Malang follows with 12."),
    ], ["semantic_query"])
    joined = "".join(result.frames)
    assert result.finish_reason == "stop", joined[-600:]
    assert len(data.requests) == 1
    answer = _answer(result)
    assert "Surabaya" in answer, joined[-600:]
    assert "native_dataset_query" not in answer

