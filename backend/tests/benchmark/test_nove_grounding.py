"""Offline cost of Nove context, reference retrieval, and a verified query trajectory."""

import pytest

from app.modules.assistant.planning import validate_turn_plan
from app.modules.assistant.registry import build_registry
from app.modules.assistant.service import AssistantLoop, LoopContext
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.tools.search_knowledge import search_references
from tests.benchmark.harness import ScriptedProvider, measure, thread
from tests.benchmark.test_assistant_loop import _print
from tests.eval.harness import evaluate, run_scenario
from tests.eval.nove_scenarios import nove_scenarios

pytestmark = pytest.mark.benchmark


async def test_nove_context_and_reference_cost():
    registry = build_registry()
    loop = AssistantLoop(provider=ScriptedProvider([]), registry=registry)

    async def context():
        ctx = LoopContext(user_name="bench", database="analytics", schema_name="public")
        ctx.selected_skills = ["debug-sql"]
        messages = loop._build_messages(thread(), "Cari penyebab query lambat", ctx)
        assert "debug-sql" in ctx.selected_skills
        assert ctx.context_stats["fits"]
        return messages

    async def skills():
        result = validate_turn_plan(
            {"intent": "sql_authoring", "tools": [], "required_tools": [],
             "skills": ["create-table"], "ml_task": None},
            set(registry.names()),
            set(skill_library.names()),
        )
        assert "create-table" in result.selected_skills

    async def references():
        result = search_references("query lambat penyebab")
        assert any(item["source"] == "knowledge:query-troubleshooting" for item in result)

    for name, operation in [
        ("nove_context", context),
        ("nove_skill_plan_validation", skills),
        ("nove_reference_search", references),
    ]:
        _print(name, await measure(operation, iterations=200))


async def test_nove_two_query_trajectory_cost():
    async def turn():
        scenario = next(s for s in nove_scenarios() if s.name == "nove_schema_then_data_query")
        result = await run_scenario(scenario)
        assert evaluate(scenario, result).passed
        assert result.tool_runs == ["query_execute", "query_execute"]
        assert result.provider_calls == 3

    _print("nove_verified_two_query_turn", await measure(turn, iterations=200))
