from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.expressions import parse_expression
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlanError, SemanticPlanner
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.unit.test_semantic_guidance_fallback import USER, guided_model, setup_tool


@pytest.mark.parametrize(
    "question",
    [
        "What is the latest month with data for revenue?",
        "Apa bulan terakhir yang punya data untuk revenue?",
    ],
)
def test_latest_month_plan_uses_data_month_and_governed_filter(question):
    model = guided_model()
    plan = SemanticPlanner().latest_month_with_data(model, question)
    assert plan is not None
    assert plan.metrics == ("total_revenue",)
    assert plan.named_filters == ("completed_order",)
    assert plan.time.dimension == "order_date"
    assert plan.time.grain == "month"
    assert plan.time.range is None
    assert plan.limit == 1
    sql = SemanticCompiler().compile(model, plan).sql
    assert "DATE_TRUNC('month', `orders`.`order_date`) AS `order_date`" in sql
    assert "ORDER BY `order_date` DESC" in sql
    assert "LIMIT 1" in sql
    assert "CURRENT_DATE" not in sql


async def test_latest_month_executes_without_provider_plan(monkeypatch):
    tool, provider, execute = setup_tool(monkeypatch)
    question = "What is the latest month with data for revenue?"
    outcome = await tool.run(
        ToolInvocation(
            tool_call_id="latest-month",
            tool_name="semantic_query",
            arguments={"question": question},
        ),
        LoopContext(user_name="alice", user=USER.copy()),
    )
    assert outcome.ok, outcome.safe_detail or outcome.error
    provider.complete.assert_not_awaited()
    assert "ORDER BY `order_date` DESC" in execute.call_args.kwargs["sql"]
    assert outcome.data["semantic_plan"]["limit"] == 1


def test_latest_month_does_not_bypass_clarification_rule():
    model = replace(
        guided_model(),
        question_routing_instructions="When 'revenue' is ambiguous, ask clarification.",
    )
    with pytest.raises(SemanticPlanError, match="requires clarification"):
        SemanticPlanner().latest_month_with_data(
            model, "What is the latest month with data for revenue?"
        )


def test_latest_month_requires_exact_metric_intent():
    assert (
        SemanticPlanner().latest_month_with_data(
            guided_model(), "What is the latest month with data for revenue in Jakarta?"
        )
        is None
    )


def test_sales_ossie_latest_month_compiles_with_filtered_metric():
    source = Path(__file__).parents[3] / "workspace/sales_agent/nova_sales_360.ossie.yaml"
    parsed = parse_ossie(source.read_text())
    assert parsed.valid, parsed.errors
    model = SemanticModelIR.from_ossie(parsed.as_dict())
    question = "What is the latest month with data for recognized revenue?"
    plan = SemanticPlanner().latest_month_with_data(model, question)
    assert plan is not None
    sql = SemanticCompiler().compile(model, plan).sql
    assert "SUM(`sales`.`net_revenue`) AS `recognized_revenue`" in sql
    assert "`sales`.`order_status` <> 'Cancelled'" in sql
    assert "ORDER BY `order_date` DESC\nLIMIT 1" in sql


def test_expression_parser_accepts_spacing_but_rejects_comments():
    parse_expression("sales.order_status <> 'Cancelled'")
    with pytest.raises(SemanticPlanError, match="Comments are not allowed"):
        parse_expression("sales.order_status <> 'Cancelled' /* hidden */")
