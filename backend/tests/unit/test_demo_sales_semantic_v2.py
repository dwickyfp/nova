"""The benchmark's demo_sales Semantic View, version 2 (rebench S1 and S2).

The edit is applied to the shared metadata by
``tests.benchmark.studio_capability.semantic_view_v2``; these tests check the
edit itself against the stored version 1 definition, offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.agents.semantic.compiler import AdditivityError, SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.serialize import to_ossie_document
from tests.benchmark.studio_capability import semantic_view_v2
from tests.benchmark.studio_capability.semantic_view_v2 import edited

V1 = json.loads((Path(semantic_view_v2.__file__).parent / "demo_sales_v1.json").read_text())


def _metrics(definition: dict) -> dict[str, dict]:
    return {metric["name"]: metric for metric in definition["metrics"]}


def _model(definition: dict) -> SemanticModelIR:
    parsed = parse_ossie(json.dumps(to_ossie_document(definition)))
    assert parsed.valid, parsed.errors
    assert not parsed.warnings
    return SemanticModelIR.from_ossie(parsed.model)


def _plan(metric: str, dimensions: list[str]) -> SemanticPlan:
    return SemanticPlan.from_dict({"metrics": [metric], "dimensions": dimensions,
                                   "filters": [], "order_by": [], "limit": 100})


def test_version_2_counts_different_customers():
    metric = _metrics(edited(V1))["customer_count"]

    assert metric["expression"] == "COUNT(DISTINCT orders.customer_id)"
    assert metric["additivity"] == "non_additive"
    assert {"pelanggan unik", "orang", "different customers"} <= set(metric["synonyms"])


def test_money_is_rupiah_and_counts_have_no_currency():
    metrics = _metrics(edited(V1))

    for name in ("revenue", "average_order_value"):
        assert (metrics[name]["unit"], metrics[name]["currency"]) == ("IDR", "IDR")
    for name in ("order_count", "customer_count"):
        assert metrics[name]["currency"] is None
    assert _metrics(V1)["revenue"]["unit"] == "USD"  # the edit does not touch its input


def test_the_edit_is_idempotent_and_keeps_every_other_metric():
    once = edited(V1)

    assert edited(once) == once
    assert [m["name"] for m in once["metrics"]] == [
        *(m["name"] for m in V1["metrics"]), "customer_count"]
    assert {k: v for k, v in once.items() if k != "metrics"} == {
        k: v for k, v in V1.items() if k != "metrics"}


def test_version_2_parses_and_compiles_overall_and_by_city():
    model = _model(edited(V1))

    total = SemanticCompiler().compile(model, _plan("customer_count", [])).sql
    by_city = SemanticCompiler().compile(model, _plan("customer_count", ["shipping_city"])).sql

    assert "COUNT(DISTINCT `orders`.`customer_id`)" in total
    assert "COUNT(DISTINCT `orders`.`customer_id`)" in by_city
    assert "shipping_city" in by_city


def test_a_distinct_count_without_allowed_dimensions_would_refuse_a_breakdown():
    """Why customer_count carries average_order_value's dimension list."""
    definition = edited(V1)
    _metrics(definition)["customer_count"]["allowed_dimensions"] = []

    with pytest.raises(AdditivityError):
        SemanticCompiler().compile(_model(definition),
                                   _plan("customer_count", ["shipping_city"]))
