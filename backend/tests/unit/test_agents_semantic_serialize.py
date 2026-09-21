"""Tests for the flat-spec -> Ossie YAML serializer (Phase 12).

An LLM reliably produces the flat spec but not the nested Ossie shape, so Nova
owns the serialization. The contract is that the output parses with the same
validator the API uses.
"""

from __future__ import annotations

from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.serialize import build_ossie_yaml

SPEC = {
    "name": "sales_analytics",
    "description": "Sales and customer analytics",
    "datasets": [
        {
            "name": "orders",
            "source": "NOVA_DEMO.orders",
            "primary_key": "order_id",
            "description": "One row per order",
            "fields": [
                {"name": "order_id", "datatype": "Integer"},
                {"name": "order_date", "datatype": "DateTime", "is_time": True},
                {"name": "total_amount", "datatype": "Decimal"},
            ],
        },
        {
            "name": "customers",
            "source": "NOVA_DEMO.customers",
            "fields": [{"name": "customer_id", "datatype": "Integer"}],
        },
    ],
    "relationships": [
        {
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        }
    ],
    "metrics": [
        {
            "name": "total_revenue",
            "expression": "SUM(orders.total_amount)",
            "datatype": "Decimal",
        }
    ],
}


def test_flat_spec_serializes_to_valid_ossie() -> None:
    yaml = build_ossie_yaml(SPEC)
    result = parse_ossie(yaml)
    assert result.valid
    assert result.version == "0.1.1"
    assert result.dataset_count == 2
    assert result.metric_count == 1
    assert result.relationship_count == 1


def test_time_dimension_flag_becomes_is_time() -> None:
    yaml = build_ossie_yaml(SPEC)
    assert "is_time: true" in yaml
    result = parse_ossie(yaml)
    order_date = next(
        f for f in result.model["datasets"][0]["fields"] if f["name"] == "order_date"
    )
    assert order_date["dimension"] == {"is_time": True}


def test_expression_is_quoted() -> None:
    # A metric expression has parentheses and a dot; it must be quoted so YAML
    # does not read it as structure.
    yaml = build_ossie_yaml(SPEC)
    assert "expression: 'SUM(orders.total_amount)'" in yaml


def test_mismatched_relationship_keys_are_dropped() -> None:
    # The spec requires equal-length key arrays; an invalid pair is omitted
    # rather than emitted as a document the parser rejects.
    spec = dict(SPEC)
    spec["relationships"] = [
        {
            "name": "bad",
            "from": "orders",
            "to": "customers",
            "from_columns": ["a", "b"],
            "to_columns": ["id"],
        }
    ]
    result = parse_ossie(build_ossie_yaml(spec), raise_on_error=False)
    assert result.relationship_count == 0
    assert result.valid


def test_empty_datasets_serializes_to_empty_list() -> None:
    yaml = build_ossie_yaml({"name": "x", "datasets": []})
    assert "datasets:\n  []" in yaml
    result = parse_ossie(yaml, raise_on_error=False)
    assert not result.valid  # no datasets is invalid, as the parser says


def test_missing_name_falls_back_to_placeholder() -> None:
    yaml = build_ossie_yaml({"datasets": []})
    assert "name: untitled_model" in yaml
