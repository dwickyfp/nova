from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.modules.access_control.service import access_control_service
from app.modules.agents import access, router, studio_router
from app.modules.agents.access import AccessItem
from app.modules.agents.repository import AgentMetadataUnavailable, agent_repository
from app.modules.agents.studio_schemas import AccessCheckRequest


def _agent() -> dict:
    now = datetime.now(UTC)
    return {
        "agent_id": "sales", "owner_name": "nova_admin", "name": "Sales Agent",
        "created_at": now, "updated_at": now,
    }


def _user(role: str) -> dict:
    return {"username": "nova_admin", "roles": ["ACCOUNTADMIN", "analyst"],
            "active_role": role}


@pytest.mark.asyncio
async def test_studio_list_and_runtime_follow_active_role(monkeypatch) -> None:
    agent = _agent()
    monkeypatch.setattr(agent_repository, "list_agents", AsyncMock(return_value=[agent]))
    monkeypatch.setattr(agent_repository, "list_shared_agents", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "get_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(access, "access_fingerprint", AsyncMock(return_value="current"))
    monkeypatch.setattr(studio_router, "access_fingerprint", AsyncMock(return_value="current"))
    monkeypatch.setattr(access, "verify_access", AsyncMock(return_value=[]))
    grants = AsyncMock(return_value=[])
    monkeypatch.setattr(agent_repository, "list_agent_roles", grants)

    studio_agents = await router.list_agents(studio=True, user=_user("ACCOUNTADMIN"))
    assert [agent.agent_id for agent in studio_agents.agents] == ["__smart__"]
    with pytest.raises(HTTPException) as denied:
        await router._require_agent("sales", _user("ACCOUNTADMIN"))
    assert denied.value.status_code == 404
    assert (await router.list_agents(studio=False, user=_user("ACCOUNTADMIN"))).count == 1

    grants.return_value = [{"role_name": "analyst", "verified_fingerprint": None}]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).count == 1
    grants.return_value = [{"role_name": "analyst", "verified_fingerprint": "current"}]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).count == 2
    assert (await router._require_agent("sales", _user("analyst")))["agent_id"] == "sales"
    assert (await router.list_agents(studio=True, user=_user("ACCOUNTADMIN"))).count == 1

    access.verify_access.return_value = [AccessItem("table", "sales.orders", False)]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).count == 1
    grants.return_value = []
    with pytest.raises(HTTPException):
        await router._require_agent("sales", _user("analyst"))


@pytest.mark.asyncio
async def test_access_read_error_retries_then_reports_unavailable(monkeypatch) -> None:
    agent = _agent()
    grant = {"role_name": "ACCOUNTADMIN", "verified_fingerprint": "current"}
    monkeypatch.setattr(
        agent_repository, "list_agent_roles", AsyncMock(return_value=[grant])
    )
    fingerprint = AsyncMock(side_effect=[RuntimeError("transient"), "current"])
    monkeypatch.setattr(access, "access_fingerprint", fingerprint)
    monkeypatch.setattr(access, "verify_access", AsyncMock(return_value=[]))

    assert await access.has_verified_access(
        agent, role_name="ACCOUNTADMIN", user=_user("ACCOUNTADMIN")
    )
    assert fingerprint.await_count == 2

    fingerprint.side_effect = RuntimeError("persistent")
    with pytest.raises(AgentMetadataUnavailable):
        await access.has_verified_access(
            agent, role_name="ACCOUNTADMIN", user=_user("ACCOUNTADMIN")
        )


@pytest.mark.asyncio
async def test_verify_only_marks_assigned_role_after_success(monkeypatch) -> None:
    agent = _agent()
    monkeypatch.setattr(studio_router, "_require_agent", AsyncMock(return_value=agent))
    grants = AsyncMock(return_value=[])
    monkeypatch.setattr(agent_repository, "list_agent_roles", grants)
    save = AsyncMock()
    monkeypatch.setattr(agent_repository, "set_agent_role_verification", save)
    monkeypatch.setattr(studio_router, "write_audit_log", AsyncMock())
    monkeypatch.setattr(access, "access_fingerprint", AsyncMock(return_value="current"))
    monkeypatch.setattr(studio_router, "access_fingerprint", AsyncMock(return_value="current"))
    verify = AsyncMock(return_value=[AccessItem("table", "sales.orders", True)])
    monkeypatch.setattr(access, "verify_access", verify)

    with pytest.raises(HTTPException) as unassigned:
        await studio_router.verify_agent_access(
            "sales", AccessCheckRequest(role_name="analyst"), _user("ACCOUNTADMIN")
        )
    assert unassigned.value.status_code == 422
    save.assert_not_awaited()

    grants.return_value = [{"role_name": "analyst"}]
    result = await studio_router.verify_agent_access(
        "sales", AccessCheckRequest(role_name="analyst"), _user("ACCOUNTADMIN")
    )
    assert result.all_granted
    assert save.await_args.kwargs["fingerprint"] == "current"

    verify.return_value = [AccessItem("table", "sales.orders", False)]
    result = await studio_router.verify_agent_access(
        "sales", AccessCheckRequest(role_name="analyst"), _user("ACCOUNTADMIN")
    )
    assert not result.all_granted
    assert save.await_args.kwargs["fingerprint"] is None


@pytest.mark.asyncio
async def test_verify_requires_actual_privilege_not_just_matching_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        access, "resolve_agent_dependencies",
        AsyncMock(return_value=[("table", "sales.orders"), ("database", "sales")]),
    )
    effective = AsyncMock(side_effect=[
        {"policies": [{"policyType": 0}], "object_access": ["INSERT"]},
        {"policies": [{"policyType": 0}], "object_access": ["USAGE"]},
    ])
    monkeypatch.setattr(access_control_service, "effective_access", effective)

    items = await access.verify_access(
        agent=_agent(), role_name="analyst", username="nova_admin",
        encrypted_password="", session_id=None,
    )

    assert [item.granted for item in items] == [False, True]
    assert effective.await_args_list[1].kwargs["resource"] == "sales.*"


def test_http_studio_and_thread_access_after_switching_roles(monkeypatch) -> None:
    agent = _agent()
    monkeypatch.setattr(agent_repository, "list_agents", AsyncMock(return_value=[agent]))
    monkeypatch.setattr(agent_repository, "list_shared_agents", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_repository, "get_agent", AsyncMock(return_value=agent))
    async def verified(_agent, *, role_name, user):
        return role_name == "analyst" and user["active_role"] == "analyst"

    monkeypatch.setattr(router, "has_verified_access", verified)
    monkeypatch.setattr(router.assistant_repository, "list_threads", AsyncMock(return_value=[]))
    role = {"active": "ACCOUNTADMIN"}
    app = FastAPI()
    app.include_router(router.router, prefix="/agents")
    app.dependency_overrides[get_current_user] = lambda: _user(role["active"])
    client = TestClient(app)

    assert client.get("/agents?studio=true").json()["count"] == 1
    assert client.get("/agents/sales/threads").status_code == 404
    role["active"] = "analyst"
    assert client.get("/agents?studio=true").json()["count"] == 2
    assert client.get("/agents/sales/threads").status_code == 200
    role["active"] = "ACCOUNTADMIN"
    assert client.get("/agents/sales/threads").status_code == 404
