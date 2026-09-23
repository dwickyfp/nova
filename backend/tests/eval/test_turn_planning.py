"""Language-independent planning and bounded tool-selection trajectories."""

from __future__ import annotations

import json

import pytest

from app.modules.assistant.planning import TurnPlanningError, plan_turn, validate_turn_plan
from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult


class PlanningProvider:
    def __init__(self, plan: dict):
        self.plan = plan
        self.messages = []

    async def complete(self, **kwargs):
        self.messages = kwargs["messages"]
        return {"content": json.dumps(self.plan)}


@pytest.mark.asyncio
async def test_planner_repairs_one_malformed_response_without_a_lexical_fallback():
    class RecoveringProvider(PlanningProvider):
        def __init__(self):
            super().__init__({})
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"content": "I would search the knowledge base."}
            assert "Repair the JSON plan" in kwargs["messages"][-1]["content"]
            return {
                "content": json.dumps(
                    {
                        "intent": "capability_help",
                        "tools": ["search_knowledge"],
                        "required_tools": [],
                        "skills": [],
                        "ml_task": None,
                    }
                )
            }

    provider = RecoveringProvider()
    plan = await plan_turn(
        provider_client=provider,
        provider=object(),
        registry=build_registry(),
        user_content="¿Qué es una vista semántica?",
    )
    assert provider.calls == 2
    assert plan.selected_tools == ("search_knowledge",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "intent", "tools", "required"),
    [
        ("Bisa bantu merancang Semantic View saya?", "capability_help", ["search_knowledge"], []),
        ("Can you help design a Semantic View?", "capability_help", ["search_knowledge"], []),
        ("Explícame cómo crear una vista semántica", "capability_help", ["search_knowledge"], []),
        (
            "Quel est le chiffre d'affaires ce mois-ci ?",
            "semantic_analytics",
            ["query_execute"],
            ["query_execute"],
        ),
        ("Zeige den Umsatz dieses Monats", "raw_sql_query", ["query_execute"], ["query_execute"]),
        ("今月の売上を表示して", "raw_sql_query", ["query_execute"], ["query_execute"]),
        ("أظهر مبيعات هذا الشهر", "raw_sql_query", ["query_execute"], ["query_execute"]),
        ("Mostre as vendas deste mês", "raw_sql_query", ["query_execute"], ["query_execute"]),
        ("이번 달 매출을 보여줘", "raw_sql_query", ["query_execute"], ["query_execute"]),
        ("Create a chart from my previous result", "chart", ["data_to_chart"], ["data_to_chart"]),
        ("Forecast sales for 30 days", "machine_learning", ["ml_execute"], ["ml_execute"]),
    ],
)
async def test_multilingual_requests_reach_the_same_provider_plan_contract(
    prompt, intent, tools, required
):
    registry = build_registry()
    provider = PlanningProvider(
        {
            "intent": intent,
            "tools": tools,
            "required_tools": required,
            "skills": [],
            "ml_task": "forecast" if intent == "machine_learning" else None,
        }
    )
    result = await plan_turn(
        provider_client=provider,
        provider=object(),
        registry=registry,
        user_content=prompt,
    )
    assert prompt in provider.messages[1]["content"]
    assert result.route.intent == intent
    assert result.selected_tools == tuple(tools)
    assert result.route.required_capabilities == tuple(required)


def test_missing_semantic_tool_maps_to_registered_read_only_query():
    plan = validate_turn_plan(
        {
            "intent": "semantic_analytics",
            "tools": [],
            "required_tools": ["semantic_query"],
            "skills": [],
            "ml_task": None,
        },
        {"query_execute"},
    )
    assert plan.selected_tools == ("query_execute",)
    assert plan.route.required_capabilities == ("query_execute",)


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "raw_sql_query", "tools": ["unknown"], "required_tools": ["unknown"]},
        {"intent": "raw_sql_query", "tools": [], "required_tools": []},
        {"intent": "capability_help", "tools": ["query_execute"], "required_tools": []},
        {"intent": "clarification", "tools": ["search_knowledge"], "required_tools": []},
        {"intent": "ui_operation", "tools": ["search_knowledge"], "required_tools": []},
        {"intent": "direct_answer", "tools": [], "required_tools": [], "skills": ["unknown"]},
    ],
)
def test_invalid_or_unauthorized_plan_fails_closed(payload):
    with pytest.raises(TurnPlanningError):
        validate_turn_plan(payload, {"query_execute", "search_knowledge"}, {"native-ml"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "Tampilkan transaksi bulan ini",
        "Show this month's transactions",
        "Montre les transactions de ce mois",
        "今月の取引を表示して",
        "أظهر معاملات هذا الشهر",
    ],
)
async def test_multilingual_data_turn_executes_required_tool_with_consent(prompt):
    registry = ToolRegistry()
    query = EvalTool("query_execute")
    registry.register(query)
    provider = ScriptedProvider(
        [tool_call_frame("query", sql="SELECT 1"), text_frame("One row.")],
        turn_plan={
            "intent": "raw_sql_query",
            "tools": ["query_execute"],
            "required_tools": ["query_execute"],
            "skills": [],
            "ml_task": None,
        },
    )
    loop = AssistantLoop(provider=provider, registry=registry, system_prompt="test")
    approved = []

    async def consent(invocation, classification):
        approved.append(classification)
        return True

    result = TurnResult(
        frames=[
            frame
            async for frame in loop.run(
                thread=thread(),
                user_content=prompt,
                context=LoopContext("bench"),
                resolve_consent=consent,
            )
        ]
    )
    assert result.finish_reason == "stop"
    assert len(query.runs) == 1
    assert approved == ["read_only"]


@pytest.mark.asyncio
async def test_studio_tool_plan_cannot_expose_unregistered_tool():
    registry = ToolRegistry()
    registry.register(EvalTool("semantic_query"))
    provider = ScriptedProvider(
        [text_frame("No query was run.")],
        turn_plan={
            "intent": "semantic_analytics",
            "tools": ["query_execute"],
            "required_tools": ["query_execute"],
            "skills": [],
            "ml_task": None,
        },
    )
    result = TurnResult(
        frames=[
            frame
            async for frame in AssistantLoop(
                provider=provider, registry=registry, system_prompt="test"
            ).run(
                thread=thread(),
                user_content="How much revenue?",
                context=LoopContext("bench"),
                resolve_consent=lambda *_: True,
            )
        ]
    )
    assert result.finish_reason == "planning_failed"
    assert "planning_failed" in result.error_codes
