"""A map of public places and routes is the answer, not a clarification.

Replayed from benchmark route_optimize-001: "Starting in Jakarta, what's the
fastest order to visit Semarang, Bandung, Yogyakarta and Surabaya by car?" The
Studio agent drew the optimised route map, but the governed data tool had been
required and reported that its catalog does not cover the request, so the
answer was replaced by "The governed data does not cover ... Which should I
use?" and the turn finished as a clarification.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.agents.prompt import _CORE_CONTRACT
from app.modules.assistant import ui_public
from app.modules.assistant.planning import _STUDIO_INSTRUCTIONS
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.state import AssistantThread
from app.modules.assistant.tools import ToolOutcome, ToolRegistry
from tests.benchmark.harness import ScriptedProvider, allow, text_frame, tool_call_frame
from tests.eval import draft_charts
from tests.eval.harness import EvalTool

QUESTION = ("Starting in Jakarta, what's the fastest order to visit Semarang, Bandung, "
            "Yogyakarta and Surabaya by car?")
#: The page the model wrote in the recorded turn, as RouteMap (checked into PlaceMap).
PAGE = """```openui
root = Page([route, note])
route = RouteMap("Optimized driving route from Jakarta", [{label: "Jakarta", lat: -6.2088, lon: 106.8456}, {label: "Bandung", lat: -6.9175, lon: 107.6191}, {label: "Semarang", lat: -6.9667, lon: 110.4167}], true, true)
note = Text("The map optimizes the stop order from Jakarta using city centers, with no return leg.")
```"""  # noqa: E501 - one statement per line is the language
#: What the recorded turn planned: the governed data tool was required.
PLAN = {"intent": "compound_analytics", "tools": ["semantic_query"],
        "required_tools": ["semantic_query"], "skills": [], "ml_task": None}
NOT_COVERED = ToolOutcome(
    ok=False, summary="", error="The governed data does not cover this request.",
    error_class="CLARIFICATION_REQUIRED", recoverable=False,
    repair_context={"measures": ["revenue", "order_count", "average_order_value"],
                    "missing_inputs": ["route"]},
)


@pytest.fixture(autouse=True)
def _roads(monkeypatch):
    ui_public._cache.clear()

    async def answer(url: str, params: dict[str, Any] | None = None) -> Any:
        if "/table/" in url:
            return {"code": "Ok", "durations": [[0, 2, 7], [2, 0, 5], [7, 5, 0]]}
        return {"code": "Ok", "routes": [{
            "geometry": {"type": "LineString",
                         "coordinates": [[106.85, -6.21], [107.62, -6.92], [110.42, -6.97]]},
            "distance": 480_000, "duration": 24_000,
        }]}

    monkeypatch.setattr(ui_public, "_public_json", answer)


def _payloads(frames: list[str], event: str) -> list[dict[str, Any]]:
    return [json.loads(frame.split("data: ", 1)[1].split("\n", 1)[0])
            for frame in frames if frame.startswith(f"event: {event}\n")]


async def _run(script: list[dict[str, Any]]) -> list[str]:
    registry = ToolRegistry()
    registry.register(EvalTool(
        "semantic_query", parameters={"type": "object",
                                      "properties": {"question": {"type": "string"}},
                                      "required": ["question"]},
        outcomes=[NOT_COVERED, NOT_COVERED]))
    context = LoopContext(user_name="alice", intelligent_ui=True,
                          user=dict(draft_charts.USER), thread_id="route-eval")
    context.agent_scope = {"agent_id": "sales-analyst"}
    provider = ScriptedProvider(script=script, turn_plan=PLAN)
    return [frame async for frame in AssistantLoop(provider=provider, registry=registry).run(
        thread=AssistantThread(thread_id="route-eval", user_name="alice", title="Eval"),
        user_content=QUESTION, context=context, resolve_consent=allow,
    )]


async def test_a_route_map_is_not_replaced_by_the_clarification_template():
    frames = await _run([
        tool_call_frame("s1", name="semantic_query", arguments={"question": QUESTION}),
        text_frame(PAGE),
    ])

    [done] = _payloads(frames, "done")
    assert done["finish_reason"] == "stop"
    program = "".join(item["text"] for item in _payloads(frames, "ui_delta"))
    assert "PlaceMap(" in program
    text = "".join(item.get("text", "") for item in _payloads(frames, "text_delta"))
    assert "does not cover" not in text and "Which should I use" not in text
    assert not _payloads(frames, "error")


async def test_without_a_page_the_gap_is_still_a_clarification():
    frames = await _run([
        tool_call_frame("s1", name="semantic_query", arguments={"question": QUESTION}),
        text_frame(""),
    ])

    [done] = _payloads(frames, "done")
    assert done["finish_reason"] == "clarification"


def test_the_planner_routes_public_places_away_from_the_data_tool():
    """P4: a visit order between named public cities needs no data tool."""
    assert "between named public places" in _STUDIO_INSTRUCTIONS
    assert "needs neither: it is direct_answer" in _STUDIO_INSTRUCTIONS
    assert "over business stops (customers, stores, orders)" in _STUDIO_INSTRUCTIONS


def test_the_studio_contract_answers_public_routes_with_a_map():
    """P3 (route_overwater-002): the out-of-scope rule has a public places exception."""
    contract = " ".join(_CORE_CONTRACT.split())
    assert "public places and the road routes between them are answered with a map" in contract
    assert "show the places and say so" in contract
