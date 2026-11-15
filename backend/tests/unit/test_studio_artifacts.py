"""Studio artifacts store reproducible queries, never cached result rows."""

from __future__ import annotations

import pytest

from app.modules.agents.artifact_repository import (
    chart_template,
    validate_artifact_sql,
)


def test_artifact_sql_must_be_one_read_only_statement() -> None:
    assert validate_artifact_sql(" SELECT 1; ") == "SELECT 1"
    with pytest.raises(ValueError, match="one read-only"):
        validate_artifact_sql("SELECT 1; SELECT 2")
    with pytest.raises(ValueError, match="Only read-only"):
        validate_artifact_sql("DROP TABLE sales.orders")


def test_chart_template_removes_every_persisted_row() -> None:
    stored = chart_template(
        {
            "mark": "bar",
            "title": "Revenue",
            "data": {"values": [{"region": "secret", "revenue": 42}]},
            "datasets": {"old": [{"region": "also-secret"}]},
            "encoding": {
                "x": {"field": "region", "type": "nominal"},
                "y": {"field": "revenue", "type": "quantitative"},
            },
        },
        title="Revenue",
    )
    assert stored["data"] == {"values": []}
    assert "datasets" not in stored
    assert "secret" not in str(stored)
