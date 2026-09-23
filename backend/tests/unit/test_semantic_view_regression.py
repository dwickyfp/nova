from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.intelligence.semantic_regression import MAX_RESULT_ROWS, compare_semantic_versions


def _fixture():
    definition = parse_ossie(
        Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    ).as_dict()
    plan = SemanticPlan(metrics=("total_revenue",))
    sql = SemanticCompiler().compile(SemanticModelIR.from_ossie(definition), plan).sql
    query = {
        "verified_query_id": "revenue", "question": "Total revenue?",
        "semantic_plan": plan.as_dict(), "verified_sql": sql,
    }
    return definition, query


@pytest.mark.asyncio
async def test_semantic_regression_compares_results_without_persisting_values():
    baseline, query = _fixture()
    candidate = deepcopy(baseline)
    candidate["metrics"][0]["expression"] = "SUM(orders.total_amount) + 1"

    async def run(sql):
        return ["total_revenue"], [[101 if "+ 1" in sql else 100]]

    report = await compare_semantic_versions(candidate, baseline, [query], run)

    assert report["changed"] == 1
    assert report["cases"][0]["status"] == "result_changed"
    assert "101" not in str(report)


@pytest.mark.asyncio
async def test_semantic_regression_ignores_row_order_when_results_match():
    baseline, query = _fixture()

    async def run(sql):
        return ["total_revenue"], [[2], [1]] if run.await_count == 1 else [[1], [2]]

    run = AsyncMock(side_effect=run)
    report = await compare_semantic_versions(baseline, baseline, [query], run)
    assert report["matched"] == 1


@pytest.mark.asyncio
async def test_semantic_regression_marks_capped_result_inconclusive():
    baseline, query = _fixture()

    async def run(sql):
        return ["total_revenue"], [[index] for index in range(MAX_RESULT_ROWS)]

    report = await compare_semantic_versions(baseline, baseline, [query], run)
    assert report["cases"][0]["status"] == "truncated"
    assert report["changed"] == 1


@pytest.mark.asyncio
async def test_semantic_publish_requires_acknowledgement_for_regression(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    service = module.SemanticViewService()
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value={
        "name": "sales", "active_version": 1,
    }))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value={
        "status": "VALIDATED", "validation": {
            "valid": True, "regression": {"changed": 1}, "baseline_version": 1,
        },
        "definition": {},
    }))
    user = {"username": "nova_admin", "active_role": "ACCOUNTADMIN"}
    with pytest.raises(HTTPException) as error:
        await service.publish("view-id", 2, user)
    assert error.value.status_code == 409

    monkeypatch.setattr(service, "_source_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "describe", AsyncMock(return_value={"active_version": 2}))
    monkeypatch.setattr(service, "_audit", AsyncMock())
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(module.db, "execute_system", execute)
    result = await service.publish("view-id", 2, user, acknowledge_regressions=True)
    assert result["active_version"] == 2
    assert execute.await_count == 3


@pytest.mark.asyncio
async def test_semantic_publish_revalidates_after_active_version_changes(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    service = module.SemanticViewService()
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value={
        "name": "sales", "active_version": 2,
    }))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value={
        "status": "VALIDATED", "validation": {
            "valid": True, "regression": {"changed": 0}, "baseline_version": 1,
        },
    }))
    with pytest.raises(HTTPException, match="Active version changed") as error:
        await service.publish("view-id", 3, {"username": "nova_admin"})
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_accountadmin_can_manage_semantic_view_owned_by_another_user(monkeypatch):
    import app.modules.intelligence.semantic_views as module

    service = module.SemanticViewService()
    monkeypatch.setattr(service, "_get", AsyncMock(return_value={
        "id": "view-id", "owner_name": "alice", "name": "sales",
    }))
    admin = await service._owned("view-id", {
        "username": "nova_admin", "active_role": "ACCOUNTADMIN",
    })
    assert admin["id"] == "view-id"
    with pytest.raises(HTTPException):
        await service._owned("view-id", {
            "username": "bob", "active_role": "ANALYST",
            "assigned_roles": ["ACCOUNTADMIN"],
        })
