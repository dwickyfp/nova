from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import router as agent_router
from app.modules.agents.rule_proposals import candidate_definition
from app.modules.agents.schemas import RuleProposalCreateRequest
from app.modules.agents.semantic.ossie import parse_ossie
from app.modules.intelligence.semantic_views import semantic_view_service


@pytest.fixture
def sales_definition() -> dict:
    source = Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
    return parse_ossie(source).as_dict()


def test_rule_candidate_compiles_both_versions(sales_definition: dict) -> None:
    candidate, old_expression, old_fp, new_fp, old_sql, new_sql = candidate_definition(
        sales_definition, "total_revenue", "SUM(orders.total_amount) - 1"
    )
    assert candidate["metrics"][0]["expression"] == "SUM(orders.total_amount) - 1"
    assert old_expression == "SUM(orders.total_amount)"
    assert old_fp != new_fp
    assert "- 1" not in old_sql
    assert "- 1" in new_sql


def test_rule_candidate_rejects_unknown_and_unsafe_expression(sales_definition: dict) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        candidate_definition(sales_definition, "missing", "SUM(orders.total_amount)")
    with pytest.raises(ValueError):
        candidate_definition(
            sales_definition, "total_revenue", "SUM(orders.total_amount); DROP TABLE orders"
        )


@pytest.mark.asyncio
async def test_rule_proposal_requires_memory_in_active_role(
    monkeypatch: pytest.MonkeyPatch, sales_definition: dict
) -> None:
    user = {
        "username": "analyst", "active_role": "sales", "assigned_roles": ["sales"],
        "session_id": "session-1",
    }
    monkeypatch.setattr(
        semantic_view_service, "get_active_for_agent",
        AsyncMock(return_value={
            "id": "model-1", "owner_name": "analyst", "version": 1,
            "definition": sales_definition,
        }),
    )
    monkeypatch.setattr(
        agent_router.agent_repository, "get_agent",
        AsyncMock(return_value={"semantic_view_ids": ["model-1"]}),
    )
    memory_get = AsyncMock(return_value=None)
    monkeypatch.setattr(agent_router.memory_repository, "get", memory_get)
    body = RuleProposalCreateRequest(
        agent_id="agent-1", memory_id="memory-1", metric_name="total_revenue",
        proposed_expression="SUM(orders.total_amount) - 1",
    )
    with pytest.raises(HTTPException) as exc:
        await agent_router.create_rule_proposal("model-1", body, user)
    assert exc.value.status_code == 404
    assert memory_get.await_args.kwargs["role_name"] == "sales"


@pytest.mark.asyncio
async def test_rule_preview_executes_both_versions_under_active_role(
    monkeypatch: pytest.MonkeyPatch, sales_definition: dict
) -> None:
    from app.modules.query.service import query_service

    _, prior_expression, old_fp, new_fp, _, _ = candidate_definition(
        sales_definition, "total_revenue", "SUM(orders.total_amount) - 1"
    )
    proposal = {
        "proposal_id": "proposal-1", "semantic_model_id": "model-1",
        "metric_name": "total_revenue", "proposed_expression": "SUM(orders.total_amount) - 1",
        "prior_expression": prior_expression, "prior_fingerprint": old_fp,
        "proposed_fingerprint": new_fp, "status": "pending", "previewed_at": None,
    }
    user = {
        "username": "analyst", "active_role": "sales", "assigned_roles": ["sales"],
        "session_id": "session-1", "encrypted_password": "sealed",
    }
    monkeypatch.setattr(
        agent_router.rule_proposal_repository, "get", AsyncMock(return_value=proposal)
    )
    monkeypatch.setattr(
        semantic_view_service, "get_active_for_agent",
        AsyncMock(return_value={
            "id": "model-1", "owner_name": "analyst", "version": 1,
            "definition": sales_definition, "database_name": "sales_db",
        }),
    )
    execute = AsyncMock(side_effect=[
        [SimpleNamespace(rows=[[100]], error=None)],
        [SimpleNamespace(rows=[[99]], error=None)],
    ])
    monkeypatch.setattr(query_service, "execute_statements", execute)
    mark = AsyncMock()
    monkeypatch.setattr(agent_router.rule_proposal_repository, "mark_previewed", mark)
    monkeypatch.setattr(agent_router, "write_audit_log", AsyncMock())

    result = await agent_router.preview_rule_proposal("model-1", "proposal-1", user)
    assert result.prior_value == "100"
    assert result.proposed_value == "99"
    assert execute.await_count == 2
    assert all(call.kwargs["role"] == "sales" for call in execute.await_args_list)
    assert all(call.kwargs["username"] == "analyst" for call in execute.await_args_list)
    mark.assert_awaited_once()


@pytest.mark.asyncio
async def test_rule_approval_requires_preview_and_rejects_stale_model(
    monkeypatch: pytest.MonkeyPatch, sales_definition: dict
) -> None:
    _, _, old_fp, new_fp, _, _ = candidate_definition(
        sales_definition, "total_revenue", "SUM(orders.total_amount) - 1"
    )
    proposal = {
        "proposal_id": "proposal-1", "semantic_model_id": "model-1",
        "metric_name": "total_revenue", "proposed_expression": "SUM(orders.total_amount) - 1",
        "prior_fingerprint": old_fp, "proposed_fingerprint": new_fp,
        "status": "pending", "previewed_at": None,
    }
    user = {"username": "analyst", "active_role": "sales", "assigned_roles": ["sales"]}
    monkeypatch.setattr(
        agent_router.rule_proposal_repository, "get", AsyncMock(return_value=proposal)
    )
    monkeypatch.setattr(
        semantic_view_service, "get_active_for_agent",
        AsyncMock(return_value={
            "id": "model-1", "owner_name": "analyst", "version": 1,
            "definition": sales_definition,
        }),
    )
    publish = AsyncMock()
    monkeypatch.setattr(semantic_view_service, "publish", publish)
    with pytest.raises(HTTPException, match="Preview the impact"):
        await agent_router.approve_rule_proposal("model-1", "proposal-1", user)
    publish.assert_not_awaited()

    proposal["previewed_at"] = "2026-09-23 00:00:00"
    proposal["prior_fingerprint"] = "stale"
    with pytest.raises(HTTPException, match="Semantic View changed"):
        await agent_router.approve_rule_proposal("model-1", "proposal-1", user)
    publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [0, 1])
async def test_rule_approval_publishes_only_without_regression_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, sales_definition: dict, changed: int
) -> None:
    _, prior_expression, old_fp, new_fp, _, _ = candidate_definition(
        sales_definition, "total_revenue", "SUM(orders.total_amount) - 1"
    )
    now = datetime(2026, 9, 24)
    proposal = {
        "proposal_id": "proposal-1", "owner_name": "analyst", "agent_id": "agent-1",
        "role_name": "sales", "memory_id": "memory-1", "semantic_model_id": "view-1",
        "metric_name": "total_revenue", "proposed_expression": "SUM(orders.total_amount) - 1",
        "prior_expression": prior_expression, "prior_fingerprint": old_fp,
        "proposed_fingerprint": new_fp, "status": "pending", "previewed_at": now,
        "reviewed_by": None, "created_at": now, "updated_at": now,
    }
    user = {
        "username": "analyst", "active_role": "sales", "assigned_roles": ["sales"],
        "encrypted_password": "sealed",
    }
    monkeypatch.setattr(
        agent_router.rule_proposal_repository, "get",
        AsyncMock(side_effect=[proposal, {**proposal, "status": "approved"}]),
    )
    set_status = AsyncMock()
    monkeypatch.setattr(agent_router.rule_proposal_repository, "set_status", set_status)
    monkeypatch.setattr(
        semantic_view_service, "get_active_for_agent",
        AsyncMock(return_value={
            "id": "view-1", "owner_name": "analyst", "version": 1,
            "definition": sales_definition,
        }),
    )
    monkeypatch.setattr(
        semantic_view_service, "describe",
        AsyncMock(return_value={"versions": [
            {"version": 1, "status": "ACTIVE", "fingerprint": old_fp}
        ]}),
    )
    add_version = AsyncMock(return_value={"version": 2})
    validate = AsyncMock(return_value={"valid": True, "regression": {"changed": changed}})
    publish = AsyncMock()
    monkeypatch.setattr(semantic_view_service, "add_version", add_version)
    monkeypatch.setattr(semantic_view_service, "validate", validate)
    monkeypatch.setattr(semantic_view_service, "publish", publish)
    monkeypatch.setattr(agent_router, "write_audit_log", AsyncMock())

    if changed:
        with pytest.raises(HTTPException, match="explicit regression review"):
            await agent_router.approve_rule_proposal("view-1", "proposal-1", user)
        publish.assert_not_awaited()
        set_status.assert_not_awaited()
    else:
        response = await agent_router.approve_rule_proposal("view-1", "proposal-1", user)
        assert response.status == "approved"
        publish.assert_awaited_once_with("view-1", 2, user)
        set_status.assert_awaited_once()
    add_version.assert_awaited_once()
    validate.assert_awaited_once_with("view-1", 2, user)
