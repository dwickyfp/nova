"""Unit tests for the Ossie semantic-model parser (Phase 12, N12-C1).

The parser is the trust boundary for user-supplied semantic definitions: it
decides whether a document may become a stored model. These tests pin the
decisions that matter:

* only the supported specification version is accepted; the known draft is
  refused with a migration hint, and an unknown version fails closed;
* structural rules the spec requires are enforced (datasets non-empty, a
  ``source`` per dataset, dialect-usable field/metric expressions, equal-length
  relationship key columns);
* a credential-shaped value is refused — a semantic model is metadata and must
  never become a place a secret is stored.

No engine, no network: the parser is pure, so these run in the fast local loop.
"""

from __future__ import annotations

import pytest

from app.modules.agents.semantic.ossie import (
    OssieParseError,
    parse_ossie,
)

#: A minimal valid 0.1.1 document.
VALID = """
version: 0.1.1
name: sales_analytics
description: Sales and customer analytics
datasets:
  - name: orders
    source: analytics.public.orders
    primary_key: [order_id]
    fields:
      - name: order_date
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: order_date
        datatype: Date
        dimension:
          is_time: true
      - name: amount
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: amount
relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]
metrics:
  - name: total_revenue
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: "SUM(orders.amount)"
    ai_context:
      synonyms: ["revenue", "total sales"]
"""


def test_valid_document_parses() -> None:
    result = parse_ossie(VALID)
    assert result.valid
    assert result.version == "0.1.1"
    assert result.dataset_count == 1
    assert result.metric_count == 1
    assert result.relationship_count == 1
    assert result.errors == []


def test_parsed_definition_carries_no_raw_version_key() -> None:
    result = parse_ossie(VALID)
    stored = result.as_dict()
    assert stored["version"] == "0.1.1"
    assert stored["name"] == "sales_analytics"
    # Fields are normalised to the resolved ANSI_SQL expression text.
    field = stored["datasets"][0]["fields"][0]
    assert field["expression"] == "order_date"
    assert field["dimension"] == {"is_time": True}
    assert stored["metrics"][0]["expression"] == "SUM(orders.amount)"


def test_known_draft_version_is_refused_with_migration_hint() -> None:
    doc = VALID.replace("0.1.1", "0.2.0.dev0")
    with pytest.raises(OssieParseError) as exc:
        parse_ossie(doc)
    assert "0.2.0.dev0" in str(exc.value)
    assert "0.1.1" in str(exc.value)


def test_unknown_version_fails_closed() -> None:
    doc = VALID.replace("0.1.1", "9.9.9")
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("Unsupported Ossie version" in e for e in result.errors)


def test_missing_version_is_refused() -> None:
    doc = "\n".join(line for line in VALID.splitlines() if "version:" not in line)
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("version" in e for e in result.errors)


def test_empty_definition_is_refused() -> None:
    result = parse_ossie("   ", raise_on_error=False)
    assert not result.valid


def test_invalid_yaml_is_refused() -> None:
    result = parse_ossie("version: 0.1.1\nname: [unclosed", raise_on_error=False)
    assert not result.valid
    assert any("not valid YAML" in e for e in result.errors)


def test_datasets_must_be_non_empty() -> None:
    doc = "version: 0.1.1\nname: x\ndatasets: []\n"
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("datasets" in e for e in result.errors)


def test_dataset_requires_source() -> None:
    doc = """
version: 0.1.1
name: x
datasets:
  - name: orders
"""
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("source" in e for e in result.errors)


def test_field_requires_usable_dialect() -> None:
    doc = """
version: 0.1.1
name: x
datasets:
  - name: orders
    source: db.sch.orders
    fields:
      - name: amount
        expression:
          dialects:
            - dialect: DAX
              expression: SUM(amount)
"""
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("dialect" in e for e in result.errors)


def test_relationship_column_lengths_must_match() -> None:
    doc = """
version: 0.1.1
name: x
datasets:
  - name: orders
    source: db.sch.orders
relationships:
  - name: bad
    from: orders
    to: customers
    from_columns: [a, b]
    to_columns: [id]
"""
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("same length" in e for e in result.errors)


def test_relationship_to_unknown_dataset_warns() -> None:
    doc = """
version: 0.1.1
name: x
datasets:
  - name: orders
    source: db.sch.orders
relationships:
  - name: r
    from: orders
    to: customers
    from_columns: [customer_id]
    to_columns: [id]
"""
    result = parse_ossie(doc, raise_on_error=False)
    assert result.valid
    assert any("unknown dataset" in w for w in result.warnings)


def test_credential_shape_is_refused() -> None:
    doc = """
version: 0.1.1
name: x
description: "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
datasets:
  - name: orders
    source: db.sch.orders
"""
    result = parse_ossie(doc, raise_on_error=False)
    assert not result.valid
    assert any("credential" in e for e in result.errors)


def test_placeholder_assignment_is_allowed() -> None:
    # A documentation placeholder is not a secret and must not block a model.
    doc = VALID.replace(
        "description: Sales and customer analytics",
        "description: 'password = ***'",
    )
    result = parse_ossie(doc)
    assert result.valid
