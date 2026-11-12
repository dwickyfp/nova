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
    assert [agent.agent_id for agent in studio_agents.agents] == []
    with pytest.raises(HTTPException) as denied:
        await router._require_agent("sales", _user("ACCOUNTADMIN"))
    assert denied.value.status_code == 404
    assert (await router.list_agents(studio=False, user=_user("ACCOUNTADMIN"))).count == 1

    grants.return_value = [{"role_name": "analyst", "verified_fingerprint": None}]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).count == 0
    grants.return_value = [{"role_name": "analyst", "verified_fingerprint": "current"}]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).count == 1
    assert (await router._require_agent("sales", _user("analyst")))["agent_id"] == "sales"
    assert (await router.list_agents(studio=True, user=_user("ACCOUNTADMIN"))).count == 0

    access.verify_access.return_value = [AccessItem("table", "sales.orders", False)]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).count == 0
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
    assert verify.await_args.kwargs["assigned_roles"] == ("ACCOUNTADMIN", "analyst")

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

    assert client.get("/agents?studio=true").json()["count"] == 0
    assert client.get("/agents/sales/threads").status_code == 404
    role["active"] = "analyst"
    assert client.get("/agents?studio=true").json()["count"] == 1
    assert client.get("/agents/sales/threads").status_code == 200
    role["active"] = "ACCOUNTADMIN"
    assert client.get("/agents/sales/threads").status_code == 404


@pytest.mark.asyncio
async def test_studio_tells_an_owner_which_of_their_agents_wait_for_access(monkeypatch) -> None:
    mine = _agent()
    theirs = {**_agent(), "agent_id": "hr", "owner_name": "bob", "name": "HR Agent"}
    monkeypatch.setattr(agent_repository, "list_agents", AsyncMock(return_value=[mine]))
    monkeypatch.setattr(agent_repository, "list_shared_agents", AsyncMock(return_value=[theirs]))
    monkeypatch.setattr(access, "access_fingerprint", AsyncMock(return_value="current"))
    monkeypatch.setattr(access, "verify_access", AsyncMock(return_value=[]))
    grants = AsyncMock(return_value=[{"role_name": "analyst", "verified_fingerprint": "stale"}])
    monkeypatch.setattr(agent_repository, "list_agent_roles", grants)

    listed = await router.list_agents(studio=True, user=_user("analyst"))
    assert [agent.agent_id for agent in listed.agents] == []
    # Another owner's agent is left out without comment: this user cannot fix it.
    assert [(gap.agent_id, gap.name) for gap in listed.needs_access] == [("sales", "Sales Agent")]
    assert (await router.list_agents(studio=False, user=_user("analyst"))).needs_access == []

    grants.return_value = [{"role_name": "analyst", "verified_fingerprint": "current"}]
    assert (await router.list_agents(studio=True, user=_user("analyst"))).needs_access == []


def _session_user(role: str = "analyst", **extra) -> dict:
    return {**_user(role), "session_id": "s-1", "security_context_version": 3, **extra}


@pytest.mark.asyncio
async def test_every_request_checks_access_again(monkeypatch) -> None:
    # Nothing is reused between requests: a grant revoked anywhere, even
    # directly in the policy store, denies on the very next request.
    agent = _agent()
    grants = AsyncMock(return_value=[{"role_name": "analyst", "verified_fingerprint": "current"}])
    monkeypatch.setattr(agent_repository, "list_agent_roles", grants)
    monkeypatch.setattr(access, "access_fingerprint", AsyncMock(return_value="current"))
    verify = AsyncMock(return_value=[AccessItem("table", "sales.orders", True)])
    monkeypatch.setattr(access, "verify_access", verify)

    user = _session_user()
    assert await access.has_verified_access(agent, role_name="analyst", user=user)
    assert await access.has_verified_access(agent, role_name="analyst", user=user)
    assert verify.await_count == 2

    verify.return_value = [AccessItem("table", "sales.orders", False)]
    assert not await access.has_verified_access(agent, role_name="analyst", user=user)


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_access_check(monkeypatch) -> None:
    import asyncio

    agent = _agent()
    monkeypatch.setattr(
        agent_repository,
        "list_agent_roles",
        AsyncMock(return_value=[{"role_name": "analyst", "verified_fingerprint": "current"}]),
    )
    monkeypatch.setattr(access, "access_fingerprint", AsyncMock(return_value="current"))
    gate = asyncio.Event()
    calls = 0

    async def slow_verify(**kwargs):
        nonlocal calls
        calls += 1
        await gate.wait()
        return [AccessItem("table", "sales.orders", True)]

    monkeypatch.setattr(access, "verify_access", slow_verify)
    user = _session_user()
    waiting = [
        asyncio.create_task(access.has_verified_access(agent, role_name="analyst", user=user))
        for _ in range(4)
    ]
    await asyncio.sleep(0.01)
    gate.set()
    assert all(await asyncio.gather(*waiting))
    assert calls == 1


async def test_capabilities_check_agents_concurrently_and_keep_order(monkeypatch):
    import asyncio

    from app.modules.agents import studio_router

    started = asyncio.Barrier(2)

    async def verified(agent, *, role_name, user):
        await asyncio.wait_for(started.wait(), 1)  # two checks must be in flight together
        return agent["agent_id"] != "denied"

    monkeypatch.setattr(studio_router, "has_verified_access", verified)
    monkeypatch.setattr("app.modules.query.service.delegated_connection_for", lambda name: None)
    agents = [{"agent_id": "a"}, {"agent_id": "denied"}, {"agent_id": "b"}, {"agent_id": "c"}]
    allowed = await studio_router._verified_agents(agents, role="ANALYST", user={"username": "u"})
    assert [a["agent_id"] for a in allowed] == ["a", "b", "c"]


async def test_capabilities_check_a_delegated_caller_in_order(monkeypatch):
    from app.modules.agents import studio_router

    order: list[str] = []

    async def verified(agent, *, role_name, user):
        order.append(agent["agent_id"])
        return True

    monkeypatch.setattr(studio_router, "has_verified_access", verified)
    monkeypatch.setattr("app.modules.query.service.delegated_connection_for", lambda name: object())
    agents = [{"agent_id": "a"}, {"agent_id": "b"}]
    await studio_router._verified_agents(agents, role="ANALYST", user={"username": "u"})
    assert order == ["a", "b"]
