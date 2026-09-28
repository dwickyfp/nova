from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.expressions import parse_expression
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_semantic_guidance_fallback import USER, guided_model, setup_tool

#: What the model plans for "the latest month with data", in any language.
LATEST_MONTH = {
    "metrics": ["total_revenue"], "dimensions": [], "filters": [], "named_filters": [],
    "time": {"dimension": "order_date", "grain": "month", "range": None, "compare": None},
    "order_by": [{"field": "order_date", "direction": "desc"}], "limit": 1,
    "unresolved_concepts": [],
}


def test_latest_month_plan_compiles_to_the_last_month_with_data():
    model = guided_model()
    plan = SemanticPlan.from_dict(LATEST_MONTH)
    sql = SemanticCompiler().compile(model, plan).sql
    assert "DATE_TRUNC('month', `orders`.`order_date`) AS `order_date`" in sql
    assert "ORDER BY `order_date` DESC" in sql
    assert "LIMIT 1" in sql
    assert "CURRENT_DATE" not in sql


@pytest.mark.parametrize(
    "question",
    ["What is the latest month with data for revenue?", "収益データがある最新の月は？"],
)
async def test_latest_month_runs_the_models_plan_with_the_governed_filter(monkeypatch, question):
    tool, provider, execute = setup_tool(monkeypatch, LATEST_MONTH)
    outcome = await tool.run(
        ToolInvocation(
            tool_call_id="latest-month",
            tool_name="semantic_query",
            arguments={"question": question},
        ),
        LoopContext(user_name="alice", user=USER.copy()),
    )
    assert outcome.ok, outcome.safe_detail or outcome.error
    provider.complete.assert_awaited_once()
    assert "ORDER BY `order_date` DESC" in execute.call_args.kwargs["sql"]
    assert outcome.data["semantic_plan"]["limit"] == 1
    assert outcome.data["semantic_plan"]["named_filters"] == ("completed_order",)


async def test_latest_month_does_not_bypass_clarification_rule():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.agents.semantic.model_planner import generate_plan

    model = replace(
        guided_model(),
        question_routing_instructions="When 'revenue' is ambiguous, ask clarification.",
    )
    provider = SimpleNamespace(resolve=AsyncMock())
    with pytest.raises(SemanticPlanError, match="requires clarification"):
        await generate_plan(
            provider, model, {}, "What is the latest month with data for revenue?",
            SimpleNamespace(),
        )
    provider.resolve.assert_not_awaited()


def test_sales_ossie_latest_month_compiles_with_filtered_metric():
    source = Path(__file__).parents[3] / "workspace/sales_agent/nova_sales_360.ossie.yaml"
    parsed = parse_ossie(source.read_text())
    assert parsed.valid, parsed.errors
    model = SemanticModelIR.from_ossie(parsed.as_dict())
    plan = SemanticPlan.from_dict({
        **LATEST_MONTH, "metrics": ["recognized_revenue"], "named_filters": ["recognized_sales"],
    })
    sql = SemanticCompiler().compile(model, plan).sql
    assert "SUM(`sales`.`net_revenue`) AS `recognized_revenue`" in sql
    assert "`sales`.`order_status` <> 'Cancelled'" in sql
    assert "ORDER BY `order_date` DESC\nLIMIT 1" in sql


def test_expression_parser_accepts_spacing_but_rejects_comments():
    parse_expression("sales.order_status <> 'Cancelled'")
    with pytest.raises(SemanticPlanError, match="Comments are not allowed"):
        parse_expression("sales.order_status <> 'Cancelled' /* hidden */")
