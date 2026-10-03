"""Stored definitions round-trip through every reviewed Autopilot change type."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.serialize import to_ossie_document
from app.modules.intelligence import context_graph
from app.modules.intelligence.autopilot import SemanticChange, apply_changes
from app.modules.intelligence.contracts import SemanticRef
from tests.benchmark.business_intelligence.model import (
    expression,
    starter_definition,
    teaching_changes,
)


@pytest.mark.parametrize(
    "change",
    [
        SemanticChange(
            kind="metric",
            name="order_count",
            definition={
                "base_dataset": "orders",
                "expression": expression("COUNT(DISTINCT orders.order_id)"),
            },
        ),
        SemanticChange(
            kind="dimension",
            name="city",
            dataset="orders",
            definition={
                "expression": expression("city"),
                "dimension": {},
                "description": "Business shipping geography",
            },
        ),
        SemanticChange(kind="synonyms", name="order_count", synonyms=["recorded orders"]),
        SemanticChange(
            kind="filter",
            name="jakarta",
            definition={"dataset": "orders", "expression": "orders.city='Jakarta'"},
        ),
        SemanticChange(
            kind="relationship",
            name="orders_customers",
            definition={
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
                "cardinality": "many_to_one",
                "preferred": True,
            },
        ),
    ],
)
def test_reviewed_changes_preserve_normalized_definitions(change):
    original = starter_definition()
    before = deepcopy(original)
    result = apply_changes(original, [change])
    assert original == before
    assert (
        SemanticModelIR.from_ossie(result).fingerprint
        != SemanticModelIR.from_ossie(original).fingerprint
    )
    restored = parse_ossie(json.dumps(to_ossie_document(result))).as_dict()
    assert (
        SemanticModelIR.from_ossie(restored).fingerprint
        == SemanticModelIR.from_ossie(result).fingerprint
    )
    assert restored["datasets"][0]["primary_key"] == original["datasets"][0]["primary_key"]


def test_dataset_candidate_and_duplicate_change_admission():
    original = starter_definition()
    dataset = deepcopy(original["datasets"][0])
    dataset["description"] = "Reviewed customer identifiers and grain"
    change = SemanticChange(kind="dataset", name=dataset["name"], definition=dataset)
    assert apply_changes(original, [change])["datasets"][0]["description"] == dataset["description"]
    with pytest.raises(ValueError, match="one change"):
        apply_changes(original, [change, change])


async def test_canonical_authority_does_not_hide_competing_domain_definitions(monkeypatch):
    definition = apply_changes(
        starter_definition(), [SemanticChange.model_validate(c) for c in teaching_changes()]
    )
    marketing = deepcopy(definition["metrics"][0])
    marketing.update(
        name="marketing_revenue",
        synonyms=["revenue"],
        owner_domain="Marketing",
        authority="supporting",
    )
    definition["metrics"].append(marketing)
    monkeypatch.setattr(
        context_graph.intelligence_service,
        "authorize_semantic",
        AsyncMock(return_value={"definition": definition}),
    )
    ref = SemanticRef(view_id="business", version=2, fingerprint="published")
    result = await context_graph.resolve_metric(ref, "revenue", {})
    assert result["status"] == "resolved" and result["selected_metric"] == "net_booked_revenue"
    assert {row["owner_domain"] for row in result["candidates"]} == {"Finance", "Marketing"}
    marketing["authority"] = "canonical"
    result = await context_graph.resolve_metric(ref, "revenue", {})
    assert result["status"] == "ambiguous" and result["selected_metric"] is None
