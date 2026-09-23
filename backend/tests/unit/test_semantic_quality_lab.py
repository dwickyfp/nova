"""Reviewed questions reveal semantic model regressions across versions."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import router
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.quality_lab import evaluate_verified_queries


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
async def test_quality_lab_scopes_model_and_queries_to_owner(monkeypatch):
    definition, queries = _fixture()
    get = AsyncMock(return_value={"definition": definition})
    list_queries = AsyncMock(return_value=queries)
    audit = AsyncMock()
    monkeypatch.setattr(router.agent_repository, "get_semantic_model", get)
    monkeypatch.setattr(router.agent_repository, "list_verified_queries", list_queries)
    monkeypatch.setattr(router, "write_audit_log", audit)
    user = {"username": "alice", "active_role": "analyst", "assigned_roles": ["analyst"]}
    result = await router.run_semantic_quality_lab("model-1", user)
    assert result.matched == 1
    get.assert_awaited_once_with("model-1", owner_name="alice")
    list_queries.assert_awaited_once_with("model-1", owner_name="alice")
    audit.assert_awaited_once()
    get.return_value = None
    with pytest.raises(HTTPException) as exc:
        await router.run_semantic_quality_lab("other-model", user)
    assert exc.value.status_code == 404
