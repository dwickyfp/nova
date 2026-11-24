"""A filter on a missing value compiles to IS [NOT] NULL, never "= NULL".

Benchmark number-026: "how many different customers ordered?" planned
``customer_id != null``; ``!= NULL`` is never true in SQL, so the query
returned no rows and the answer said there was no data.
"""

from __future__ import annotations

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.plan_contract import validate_generated_plan
from app.modules.agents.semantic.planning import SemanticFilter, SemanticPlan, SemanticPlanError
from tests.unit.test_semantic_intelligence import sales_model


@pytest.mark.parametrize(("operator", "predicate"), [
    ("!=", "`orders`.`city` IS NOT NULL"),
    ("=", "`orders`.`city` IS NULL"),
    ("IS NOT NULL", "`orders`.`city` IS NOT NULL"),
    ("IS NULL", "`orders`.`city` IS NULL"),
])
def test_a_missing_value_filter_compiles_to_is_null(operator, predicate):
    plan = SemanticPlan(metrics=("total_revenue",),
                        filters=(SemanticFilter("city", operator, None),))
    sql = SemanticCompiler().compile(sales_model(), plan).sql

    assert predicate in sql
    assert "= NULL" not in sql and "!= NULL" not in sql


def test_an_ordering_comparison_with_a_missing_value_is_refused():
    plan = SemanticPlan(metrics=("total_revenue",),
                        filters=(SemanticFilter("city", ">", None),))
    with pytest.raises(SemanticPlanError, match="IS NULL or IS NOT NULL"):
        SemanticCompiler().compile(sales_model(), plan)


@pytest.mark.parametrize("operator", ["IS NULL", "IS NOT NULL", "NOT IN"])
def test_the_generated_plan_contract_offers_every_compiled_operator(operator):
    validate_generated_plan({
        "metrics": ["total_revenue"], "dimensions": [],
        "filters": [{"field": "city", "operator": operator,
                     "value": ["Jakarta"] if operator == "NOT IN" else None}],
        "named_filters": [], "time": None, "order_by": [], "limit": None,
        "unresolved_concepts": [],
    })


@pytest.mark.parametrize(("filter_item", "message"), [
    ({"field": "city", "operator": "NOT IN", "value": []}, "NOT IN requires"),
    ({"field": "city", "operator": "IS NULL", "value": "Jakarta"}, "takes no value"),
])
def test_the_contract_refuses_malformed_new_operators(filter_item, message):
    with pytest.raises(SemanticPlanError, match=message):
        validate_generated_plan({
            "metrics": ["total_revenue"], "dimensions": [], "filters": [filter_item],
            "named_filters": [], "time": None, "order_by": [], "limit": None,
            "unresolved_concepts": [],
        })
