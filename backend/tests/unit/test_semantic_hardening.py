"""Regression boundaries for governed planning and provider contracts."""

from dataclasses import replace

import pytest

from app.modules.agents.semantic.compiler import MultiFactCompilationError, SemanticCompiler
from app.modules.agents.semantic.expressions import qualify_expression
from app.modules.agents.semantic.ir import SemanticMetricIR, SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError, SemanticPlanner
from app.modules.agents.semantic.runtime import scope_semantic_model, semantic_ir_to_definition
from app.modules.assistant.intelligence import (
    EvidenceTracker,
    SemanticRoutingIndex,
    TurnRouter,
    enforce_evidence,
    validate_json_arguments,
)
from tests.unit.test_semantic_intelligence import sales_model


def test_unknown_filter_cannot_be_dropped_or_receive_high_confidence():
    model = sales_model()
    result = SemanticPlanner().plan(model, "Revenue Enterprise this month")
    assert result.confidence.unresolved == ("Enterprise",)
    assert result.confidence.level == "low"
    with pytest.raises(SemanticPlanError):
        SemanticCompiler().compile(model, result.plan)


def test_yoy_selects_two_disjoint_months_not_entire_year():
    model = sales_model()
    plan = SemanticPlanner().plan(model, "Revenue this month YoY").plan
    assert plan.time.compare == "year_over_year"
    sql = SemanticCompiler().compile(model, plan).sql
    assert "INTERVAL 1 YEAR" in sql
    assert ") OR (" in sql
    assert sql.count("< DATE_") == 2


def test_catalog_scope_filters_expression_dependencies_and_round_trips_facts():
    model = sales_model()
    leaked = SemanticMetricIR("secret_metric", "SUM(regions.secret)", "orders")
    dependent = SemanticMetricIR(
        "dependent", "SUM(amount)", "orders", dependencies=("secret_metric",)
    )
    model = replace(model, metrics=(*model.metrics, leaked, dependent))
    scoped = scope_semantic_model(model, {"orders"})
    assert [item.name for item in scoped.metrics] == ["total_revenue"]
    assert not scoped.relationships
    assert not scope_semantic_model(model, set()).datasets
    round_trip = SemanticModelIR.from_ossie(semantic_ir_to_definition(scoped))
    assert round_trip.field("amount").kind == model.field("amount").kind


def test_fact_metrics_refuse_unproven_combination():
    model = sales_model()
    model = replace(
        model,
        metrics=(*model.metrics, SemanticMetricIR("population", "SUM(population)", "regions")),
    )
    with pytest.raises(MultiFactCompilationError):
        SemanticCompiler().compile(model, SemanticPlan(metrics=("total_revenue", "population")))


@pytest.mark.parametrize(
    "expression", ["amount; DROP TABLE t", "amount -- comment", "(SELECT amount FROM secret)"]
)
def test_expression_parser_rejects_unbounded_sql(expression):
    with pytest.raises(SemanticPlanError):
        qualify_expression(expression, "orders")


def test_expression_qualification_preserves_literals_and_functions():
    expression = "SUM(CASE WHEN status = 'amount' THEN amount ELSE 0 END)"
    qualified = qualify_expression(expression, "orders")
    assert "`orders`.`status` = 'amount'" in qualified
    assert "THEN `orders`.`amount`" in qualified
    assert qualified.startswith("SUM(CASE")


def test_nested_effective_schema_validates_nullable_arrays_and_unknown_keys():
    schema = {
        "type": "object",
        "required": ["limit", "filters"],
        "additionalProperties": False,
        "properties": {
            "limit": {"type": ["integer", "null"], "minimum": 1},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["field"],
                    "additionalProperties": False,
                    "properties": {"field": {"type": "string"}},
                },
            },
        },
    }
    assert not validate_json_arguments(schema, {"limit": None, "filters": [{"field": "city"}]})
    errors = validate_json_arguments(
        schema, {"limit": True, "filters": [{"field": 4, "sql": "DROP"}]}
    )
    assert {item.path for item in errors} == {"limit", "filters[0].field", "filters[0].sql"}


def test_custom_metric_routes_and_qualitative_claim_requires_evidence():
    index = SemanticRoutingIndex.from_terms({"retained_arr"})
    assert TurnRouter().route("retained ARR this month", semantic_index=index).needs_data
    assert TurnRouter().route("What is revenue this month?").needs_data
    answer = "Enterprise is our strongest segment."
    assert enforce_evidence(answer, needs_data=True, evidence=EvidenceTracker()) != answer
