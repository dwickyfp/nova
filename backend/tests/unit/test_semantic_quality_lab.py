"""Reviewed questions reveal semantic model regressions across versions."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.quality_lab import evaluate_verified_queries
from app.modules.intelligence.semantic_views import (
    quality_semantic_view,
    semantic_view_service,
)


def _fixture() -> tuple[dict, list[dict]]:
    definition = parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()
    plan = SemanticPlan(metrics=("total_revenue",))
    sql = SemanticCompiler().compile(SemanticModelIR.from_ossie(definition), plan).sql
    queries = [{
        "verified_query_id": "vq-1", "question": "What is total revenue?",
        "semantic_plan": plan.as_dict(), "verified_sql": sql,
    }]
    return definition, queries


def test_quality_lab_flags_a_metric_rule_change() -> None:
    definition, queries = _fixture()
    baseline = evaluate_verified_queries(definition, queries)
    assert (baseline["total"], baseline["matched"], baseline["changed"]) == (1, 1, 0)
    changed = deepcopy(definition)
    changed["metrics"][0]["expression"] = "SUM(orders.total_amount) - 1"
    result = evaluate_verified_queries(changed, queries)
    assert (result["matched"], result["changed"]) == (0, 1)
    assert result["cases"][0]["status"] == "changed"


@pytest.mark.asyncio
async def test_quality_lab_scopes_view_version_and_queries_to_owner(monkeypatch):
    definition, queries = _fixture()
    definition["verified_queries"] = queries
    view = {
        "id": "view-1", "name": "Sales", "status": "ACTIVE", "visibility": "PRIVATE",
        "owner_name": "alice", "active_version": 1,
    }
    get_view = AsyncMock(side_effect=lambda view_id: view if view_id == "view-1" else None)
    version = {
        "status": "ACTIVE", "definition": definition,
        "fingerprint": SemanticModelIR.from_ossie(definition).fingerprint,
    }
    get_version = AsyncMock(side_effect=lambda view_id, number: (
        version if view_id == "view-1" and number == 1 else None
    ))
    monkeypatch.setattr(semantic_view_service, "_get", get_view)
    monkeypatch.setattr(semantic_view_service, "_version", get_version)
    monkeypatch.setattr(semantic_view_service, "_source_access", AsyncMock(return_value=True))
    monkeypatch.setattr(semantic_view_service, "_entity_access", AsyncMock(return_value=True))
    audit = AsyncMock()
    monkeypatch.setattr(semantic_view_service, "_audit", audit)
    user = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"]}
    result = await quality_semantic_view("view-1", 1, user)
    assert result["quality_lab"]["matched"] == 1
    assert result["quality"]["verified_query_count"] == 1
    assert result["verified_queries"][0]["verified_query_id"] == "vq-1"
    audit.assert_awaited_once_with("QUALITY", "Sales", user)
    get_view.assert_awaited_once_with("view-1")
    get_version.assert_awaited_once_with("view-1", 1)
    with pytest.raises(HTTPException) as exc:
        await quality_semantic_view("view-1", 1, {**user, "username": "bob"})
    assert exc.value.status_code == 404
    get_version.assert_awaited_once_with("view-1", 1)
    with pytest.raises(HTTPException) as exc:
        await quality_semantic_view("other-view", 1, user)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await quality_semantic_view("view-1", 2, user)
    assert exc.value.status_code == 404
