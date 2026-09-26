from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.decision import DecisionSession
from app.modules.assistant.intelligence import TurnIntent, TurnRoute
from app.modules.assistant.planning import TurnPlan, refine_turn_plan
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.tools import ToolRegistry
from tests.benchmark.harness import ScriptedProvider, text_frame, thread, tool_call_frame
from tests.eval.harness import EvalTool, TurnResult
from tests.unit.test_decision_runtime import settings


@pytest.mark.parametrize("enabled", [False, True])
async def test_decision_failure_preserves_consent_and_evidence_path(monkeypatch, enabled):
    session = DecisionSession(settings()) if enabled else None
    if session:
        session._post = AsyncMock(side_effect=TimeoutError("secret"))
    monkeypatch.setattr(
        "app.modules.assistant.decision.decision_session", AsyncMock(return_value=session)
    )
    tool = EvalTool("query_execute", classification="destructive")
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider(
        [
            tool_call_frame("q", name="query_execute", arguments={"sql": "DELETE FROM facts"}),
            text_frame("Stopped."),
        ],
        turn_plan={
            "intent": "raw_sql_query",
            "tools": ["query_execute"],
            "required_tools": ["query_execute"],
        },
    )
    context = LoopContext(user_name="alice", agent_id="finance")
    consent = AsyncMock(return_value=False)
    loop = AssistantLoop(provider=provider, registry=registry, max_iterations=3)
    frames = [
        frame
        async for frame in loop.run(
            thread=thread(),
            user_content="Delete the facts",
            context=context,
            resolve_consent=consent,
        )
    ]
    assert consent.await_count == 1
    assert not tool.runs
    assert "secret" not in "".join(frames)
    assert TurnResult(frames=frames).finish_reason == "denied"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("fail_after_tool", [False, True])
async def test_model_selection_preserves_provider_error_and_tool_effects(
    monkeypatch, enabled, fail_after_tool,
):
    session = DecisionSession(settings()) if enabled else None
    if session:
        session.workload = AsyncMock(return_value={"provider_id": "routed", "name": "routed"})
        session.relevance = AsyncMock(return_value={})
    monkeypatch.setattr("app.modules.assistant.decision.decision_session",
                        AsyncMock(return_value=session))
    tool = EvalTool("query_execute", table={"columns": ["count"], "rows": [[128]]})
    registry = ToolRegistry()
    registry.register(tool)
    provider = ScriptedProvider([], turn_plan={
        "intent": "raw_sql_query", "tools": ["query_execute"],
        "required_tools": ["query_execute"],
    })
    provider.resolve = AsyncMock(wraps=provider.resolve)
    calls = []

    async def stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == (2 if fail_after_tool else 1):
            yield "delta", "discard this incomplete answer"
            raise TimeoutError("provider failed")
        if (fail_after_tool and len(calls) == 1) or len(calls) == 2:
            yield "message", tool_call_frame(
                "q", name="query_execute", arguments={"sql": "SELECT COUNT(*) FROM orders"},
            )
        else:
            yield "delta", "The count is 128."
            yield "message", text_frame("The count is 128.")

    provider.stream = stream
    context = LoopContext(user_name="alice", agent_id="finance")
    consent = AsyncMock(return_value=True)
    loop = AssistantLoop(provider=provider, registry=registry, max_iterations=4)
    frames = [frame async for frame in loop.run(
        thread=thread(), user_content="Query the order count", context=context,
        provider_id="original", model="original", resolve_consent=consent,
    )]
    assert TurnResult(frames=frames).finish_reason == "error"
    assert provider.resolve.await_count == 1
    assert len(tool.runs) == int(fail_after_tool)
    assert consent.await_count == int(fail_after_tool)
    assert len(calls) == (2 if fail_after_tool else 1)
    assert "discard this incomplete answer" not in "".join(frames)


async def test_jev_cannot_remove_required_tool_or_add_unregistered_tool():
    registry = ToolRegistry()
    registry.register(EvalTool("query_execute"))
    registry.register(EvalTool("search_knowledge"))
    plan = TurnPlan(
        TurnRoute(
            intent=TurnIntent.RAW_SQL_QUERY,
            needs_data=True,
            required_capabilities=("query_execute",),
        ),
        ("query_execute", "search_knowledge"),
    )
    decision = AsyncMock()
    decision.relevance.return_value = {
        "tool:query_execute": 0,
        "tool:search_knowledge": 0,
        "tool:invented": 2,
    }
    result = await refine_turn_plan(plan, registry, "Show current orders", decision)
    assert result.selected_tools == ("query_execute",)
    assert result.route.required_capabilities == ("query_execute",)


async def test_jev_cannot_turn_sql_drafting_into_execution():
    registry = ToolRegistry()
    registry.register(EvalTool("query_execute"))
    registry.register(EvalTool("validate_sql"))
    plan = TurnPlan(TurnRoute(intent=TurnIntent.SQL_AUTHORING), ("validate_sql",))
    decision = AsyncMock()
    decision.relevance.return_value = {"tool:query_execute": 2}
    result = await refine_turn_plan(plan, registry, "Draft SQL", decision)
    assert "query_execute" not in result.selected_tools


async def test_uncertain_skill_confirmation_retains_baseline_and_rejects_additions():
    from types import SimpleNamespace

    registry = ToolRegistry()
    registry.register(EvalTool("load_skill"))
    registry.discoverable_skills = ("source", "extra")
    registry.skill_definitions = {
        name: SimpleNamespace(summary=name, body=f"Instructions for {name}")
        for name in registry.discoverable_skills
    }
    plan = TurnPlan(TurnRoute(intent=TurnIntent.DIRECT_ANSWER), ("load_skill",), ("source",))
    decision = AsyncMock()
    decision.relevance.side_effect = [{"skill:source": 0, "skill:extra": 2}, {}]
    result = await refine_turn_plan(plan, registry, "Explain the task", decision)
    assert result.selected_skills == ("source",)
    assert result.selected_tools == ("load_skill",)


@pytest.mark.parametrize("unavailable", [False, True])
async def test_workload_routing_uses_registered_target_or_original_model(monkeypatch, unavailable):
    session = DecisionSession(settings())
    session.workload = AsyncMock(return_value={"provider_id": "heavy-provider", "name": "heavy"})
    session.relevance = AsyncMock(return_value={})
    monkeypatch.setattr(
        "app.modules.assistant.decision.decision_session", AsyncMock(return_value=session)
    )
    provider = ScriptedProvider(
        [text_frame("Hello")],
        turn_plan={
            "intent": "direct_answer",
            "tools": [],
            "required_tools": [],
        },
    )
    original_resolve = provider.resolve
    calls = []

    async def resolve(**kwargs):
        calls.append(kwargs)
        if unavailable and kwargs.get("model") == "heavy":
            raise ValueError("Unavailable")
        return await original_resolve(**kwargs)

    provider.resolve = resolve
    context = LoopContext(user_name="alice", agent_id="finance")
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    frames = [
        frame
        async for frame in loop.run(
            thread=thread(),
            user_content="Hello",
            context=context,
            provider_id="original-provider",
            model="original",
            resolve_consent=AsyncMock(),
        )
    ]
    assert TurnResult(frames=frames).finish_reason == "stop"
    assert calls[0] == {"provider_id": "heavy-provider", "model": "heavy"}
    if unavailable:
        assert calls[1] == {"provider_id": "original-provider", "model": "original"}
    else:
        assert context.model_name == "heavy"
        assert context.model_provider_id == "heavy-provider"


@pytest.mark.parametrize("enabled", [False, True])
async def test_model_selection_preserves_planner_error_path(monkeypatch, enabled):
    session = DecisionSession(settings()) if enabled else None
    if session:
        session.workload = AsyncMock(return_value={"provider_id": "slow-provider", "name": "slow"})
        session.relevance = AsyncMock(return_value={})
    monkeypatch.setattr("app.modules.assistant.decision.decision_session",
                        AsyncMock(return_value=session))
    provider = ScriptedProvider([text_frame("Hello")])
    provider.plan_turn = AsyncMock(side_effect=TimeoutError("secret"))
    context = LoopContext(user_name="alice", agent_id="finance")
    loop = AssistantLoop(provider=provider, registry=ToolRegistry())
    frames = [frame async for frame in loop.run(
        thread=thread(), user_content="Hello", context=context,
        provider_id="original-provider", model="original", resolve_consent=AsyncMock(),
    )]
    assert TurnResult(frames=frames).finish_reason == "planning_failed"
    assert provider.plan_turn.await_count == 1
    assert "secret" not in "".join(frames)
