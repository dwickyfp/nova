"""Semantic View extensions reuse the existing deterministic compiler."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir


def definition() -> dict:
    return parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()


def test_derived_metric_compiles_base_metrics_without_an_llm():
    source = definition()
    source["metrics"].append(
        {
            "name": "revenue_per_order",
            "expression": "total_revenue / order_count",
            "base_dataset": "orders",
            "dependencies": ["total_revenue", "order_count"],
        }
    )
    model = SemanticModelIR.from_ossie(source)
    assert validate_semantic_model_ir(model).valid
    sql = SemanticCompiler().compile(
        model, SemanticPlan(metrics=("revenue_per_order",))
    ).sql
    assert "SUM(" in sql and "COUNT(DISTINCT" in sql
    assert "AS `revenue_per_order`" in sql


def test_hierarchy_references_existing_dimensions_in_order():
    source = definition()
    customers = next(dataset for dataset in source["datasets"] if dataset["name"] == "customers")
    for field in customers["fields"]:
        if field["name"] in {"country", "city"}:
            field["kind"] = "dimension"
    source["hierarchies"] = {"geography": ["customers.country", "customers.city"]}
    model = SemanticModelIR.from_ossie(source)
    assert model.hierarchies[0].dimensions == ("customers.country", "customers.city")
    assert validate_semantic_model_ir(model).valid
    changed = deepcopy(source)
    changed["hierarchies"] = {"geography": ["customers.country", "missing"]}
    assert not validate_semantic_model_ir(SemanticModelIR.from_ossie(changed)).valid


@pytest.mark.parametrize(
    "expression,dependencies",
    [
        ("revenue_per_order + total_revenue", ["revenue_per_order", "total_revenue"]),
        ("unknown_metric / order_count", ["unknown_metric", "order_count"]),
        ("total_revenue + 1", ["total_revenue", "order_count"]),
    ],
)
def test_derived_metric_validation_rejects_cycle_unknown_and_unused(
    expression, dependencies
):
    source = definition()
    source["metrics"].append(
        {
            "name": "revenue_per_order",
            "expression": expression,
            "base_dataset": "orders",
            "dependencies": dependencies,
        }
    )
    assert not validate_semantic_model_ir(SemanticModelIR.from_ossie(source)).valid


def test_derived_metric_rejects_non_numeric_dependency():
    source = definition()
    source["metrics"].append({
        "name": "category_label", "expression": "MAX(products.category)",
        "base_dataset": "products", "datatype": "String",
    })
    source["metrics"].append({
        "name": "bad_math", "expression": "category_label / category_label",
        "base_dataset": "products", "datatype": "Decimal",
        "dependencies": ["category_label"],
    })
    report = validate_semantic_model_ir(SemanticModelIR.from_ossie(source))
    assert not report.valid
    assert any("not numeric" in error for error in report.errors)


def test_semantic_metric_rejects_unknown_additivity_instead_of_assuming_additive():
    source = definition()
    source["metrics"][0]["additivity"] = "sometimes_additive"
    with pytest.raises(ValueError, match="unsupported additivity"):
        SemanticModelIR.from_ossie(source)


@pytest.mark.asyncio
async def test_shared_entity_key_must_match_semantic_dataset_grain(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    source = definition()
    source["entities"] = {"customer": "entity-id"}
    model = SemanticModelIR.from_ossie(source)
    monkeypatch.setattr(module.entity_registry, "get", AsyncMock(return_value=SimpleNamespace(
        relation="NOVA_DEMO.customers", key_columns=["customer_id"],
    )))
    assert await module.SemanticViewService._entity_access(model, {})
    module.entity_registry.get.return_value.key_columns = ["missing_id"]
    assert not await module.SemanticViewService._entity_access(model, {})
