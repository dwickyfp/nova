import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.planning import SemanticPlanError, SemanticPlanner
from app.modules.agents.semantic.runtime import SemanticCatalogRetriever, semantic_ir_to_definition
from app.modules.agents.tools.semantic_query import SemanticQueryTool
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_semantic_intelligence import sales_model
from tests.unit.test_semantic_plan_contract import _plan

QUESTION = "Bandingkan revenue per city untuk tahun 2025"
USER = {
    "username": "alice",
    "encrypted_password": "private-test-value",
    "active_role": "analyst",
    "assigned_roles": ["analyst"],
}


def guided_model():
    return replace(
        sales_model(),
        question_routing_instructions="Route commercial KPI and revenue questions to this model.",
        query_generation_instructions=(
            "Use order_date as the default time dimension.\n"
            "Always apply named filter 'completed_order'."
        ),
    )


def generated_plan():
    return {
        **_plan(),
        "dimensions": ["city"],
        "time": {"dimension": "order_date", "grain": None, "range": "2025", "compare": None},
    }


def setup_tool(monkeypatch, plan=None):
    from app.modules.agents.repository import agent_repository
    from app.modules.query.service import query_service

    provider = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(
                capabilities=SimpleNamespace(supports_json_schema=True),
            )
        ),
        complete=AsyncMock(return_value={"content": json.dumps(plan or generated_plan())}),
    )
    tool = SemanticQueryTool(provider=provider)
    monkeypatch.setattr(
        tool,
        "_resolve_model",
        AsyncMock(
            return_value={
                "semantic_model_id": "sales",
                "definition": semantic_ir_to_definition(guided_model()),
            }
        ),
    )
    monkeypatch.setattr(agent_repository, "list_verified_queries", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "record_semantic_usage", AsyncMock())
    execute = AsyncMock(
        return_value=[
            SimpleNamespace(
                columns=["city", "total_revenue"],
                rows=[["Jakarta", 100]],
                row_count=1,
                error=None,
            )
        ]
    )
    monkeypatch.setattr(query_service, "execute_statements", execute)
    return tool, provider, execute


@pytest.mark.parametrize("question", [QUESTION, "Compare revenue by city for 2025"])
def test_calendar_year_is_bounded_without_period_comparison(question):
    model = sales_model()
    planned = SemanticPlanner().plan(model, question)
    assert planned.confidence.unresolved_count == 0
    assert planned.plan.time.range == "2025"
    assert planned.plan.time.compare is None
    assert planned.plan.time.grain is None
    sql = SemanticCompiler().compile(model, planned.plan).sql
    assert ">= '2025-01-01'" in sql
    assert "< '2026-01-01'" in sql


def test_catalog_includes_default_time_dimension_without_lexical_match():
    catalog = SemanticCatalogRetriever().retrieve(sales_model(), "Revenue", limit=1)
    assert "order_date" in {field["name"] for field in catalog.dimensions}


async def test_natural_guidance_uses_validated_fallback_and_required_filters(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch)
    outcome = await tool.run(
        ToolInvocation(
            tool_call_id="semantic-1", tool_name="semantic_query", arguments={"question": QUESTION}
        ),
        LoopContext(user_name="alice", user=USER.copy()),
    )
    assert outcome.ok, outcome.safe_detail or outcome.error
    provider.complete.assert_awaited_once()
    assert outcome.data["semantic_plan"]["named_filters"] == ("completed_order",)
    assert execute.call_args.kwargs["username"] == "alice"
    assert ">= '2025-01-01'" in execute.call_args.kwargs["sql"]
    assert "private-test-value" not in str(provider.complete.call_args)


@pytest.mark.parametrize(
    "patch",
    [
        {"time": None},
        {"metrics": ["payroll"]},
        {"unresolved_concepts": [{"text": "margin", "type_hint": "metric", "material": True}]},
        {"sql": "SELECT * FROM payroll"},
    ],
)
async def test_invalid_fallback_never_executes(monkeypatch, patch):
    tool, _, execute = setup_tool(monkeypatch, {**generated_plan(), **patch})
    outcome = await tool.run(
        ToolInvocation(
            tool_call_id="semantic-1", tool_name="semantic_query", arguments={"question": QUESTION}
        ),
        LoopContext(user_name="alice", user=USER.copy()),
    )
    assert not outcome.ok
    assert outcome.error_class == "INVALID_SEMANTIC_PLAN"
    execute.assert_not_awaited()


async def test_fallback_still_blocks_explicit_clarification_rule():
    model = replace(
        guided_model(),
        question_routing_instructions=(
            "Route revenue questions to this model.\n"
            "When 'customer' is ambiguous, ask clarification."
        ),
    )
    provider = SimpleNamespace(resolve=AsyncMock())
    with pytest.raises(SemanticPlanError, match="requires clarification"):
        await SemanticQueryTool(provider=provider)._generate_plan(
            model,
            {},
            "Revenue per customer",
            SimpleNamespace(),
        )
    provider.resolve.assert_not_awaited()
