"""Access verification reads Ranger once per check and runs independent checks together."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app.integrations.ranger.client import RangerClient, RangerError, read_snapshot
from app.modules.agents import access, router


def _ranger(calls: list[str], *, fail_first: bool = False) -> RangerClient:
    state = {"failed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if fail_first and not state["failed"]:
            state["failed"] = True
            return httpx.Response(400)
        if request.url.path.endswith("/policy"):
            return httpx.Response(200, json=[{"name": "p", "policyItems": [{"roles": ["r"]}]}])
        if request.url.path.endswith("/xusers/users/"):
            return httpx.Response(
                200, json={"vXUsers": [{"name": "ana", "otherAttrsMap": {"region": "west"}}]}
            )
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(
        base_url="http://ranger.invalid", transport=httpx.MockTransport(handler)
    )
    return RangerClient(http)


@pytest.mark.asyncio
async def test_reads_are_shared_inside_one_scope_only() -> None:
    calls: list[str] = []
    client = _ranger(calls)

    async with read_snapshot():
        first, second = await asyncio.gather(client.list_policies(), client.list_policies())
        attributes = await client.get_user_attributes("ana")
        assert await client.get_user_attributes("ana") == attributes == {"region": "west"}
    assert first == second
    assert calls.count("GET /service/public/v2/api/policy") == 1
    assert calls.count("GET /service/xusers/users/") == 1

    # Outside a scope every read goes to Ranger, so no state outlives a check.
    await client.list_policies()
    async with read_snapshot():
        await client.list_policies()
    assert calls.count("GET /service/public/v2/api/policy") == 3


@pytest.mark.asyncio
async def test_shared_reads_are_copies_and_writes_or_failures_are_not_remembered() -> None:
    calls: list[str] = []
    client = _ranger(calls, fail_first=True)

    async with read_snapshot():
        with pytest.raises(RangerError):
            await client.list_policies()
        policies = await client.list_policies()
        policies[0]["policyItems"][0]["roles"].append("intruder")
        assert (await client.list_policies())[0]["policyItems"][0]["roles"] == ["r"]
        await client.delete_policy(7)
        await client.list_policies()
    assert calls.count("GET /service/public/v2/api/policy") == 3


@pytest.mark.asyncio
async def test_verify_access_checks_dependencies_together_in_dependency_order(monkeypatch) -> None:
    deps = [("table", "a.t1"), ("table", "a.t2"), ("table", "a.t3"), ("database", "a")]
    monkeypatch.setattr(access, "resolve_agent_dependencies", AsyncMock(return_value=deps))
    monkeypatch.setattr(access, "_semantic_pins", AsyncMock(return_value=None))
    running = {"now": 0, "peak": 0}

    async def effective_access(*, principal: str, active_role: str, resource: str) -> dict:
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        allowed = {"a.t1": ["SELECT"], "a.t2": [], "a.t3": ["ALL"], "a.*": ["USAGE"]}
        return {"object_access": allowed[resource]}

    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(access_control_service, "effective_access", effective_access)
    items = await access.verify_access(
        agent={"agent_id": "x"}, role_name="analyst", username="ana",
        encrypted_password="", session_id=None,
    )
    assert [(item.name, item.granted) for item in items] == [
        ("a.t1", True), ("a.t2", False), ("a.t3", True), ("a", True),
    ]
    # Bounded: a few checks at a time, never one connection per dependency at once.
    assert 1 < running["peak"] <= 3


@pytest.mark.asyncio
async def test_verify_access_raises_the_first_failing_dependency(monkeypatch) -> None:
    deps = [("table", "a.ok"), ("table", "a.first"), ("table", "a.second")]
    monkeypatch.setattr(access, "resolve_agent_dependencies", AsyncMock(return_value=deps))
    monkeypatch.setattr(access, "_semantic_pins", AsyncMock(return_value=None))

    async def effective_access(*, principal: str, active_role: str, resource: str) -> dict:
        if resource == "a.second":
            raise RuntimeError("second")
        if resource == "a.first":
            await asyncio.sleep(0.01)
            raise RuntimeError("first")
        return {"object_access": ["SELECT"]}

    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(access_control_service, "effective_access", effective_access)
    with pytest.raises(RuntimeError, match="first"):
        await access.verify_access(
            agent={"agent_id": "x"}, role_name="analyst", username="ana",
            encrypted_password="", session_id=None,
        )


@pytest.mark.asyncio
async def test_verify_access_stays_sequential_on_a_delegated_connection(monkeypatch) -> None:
    deps = [("table", "a.t1"), ("table", "a.t2"), ("table", "a.t3")]
    monkeypatch.setattr(access, "resolve_agent_dependencies", AsyncMock(return_value=deps))
    monkeypatch.setattr(access, "_semantic_pins", AsyncMock(return_value=None))
    from app.modules.query import service as query_service

    monkeypatch.setattr(query_service, "delegated_connection_for", lambda username: object())
    running = {"now": 0, "peak": 0}

    async def effective_access(*, principal: str, active_role: str, resource: str) -> dict:
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.005)
        running["now"] -= 1
        return {"object_access": ["SELECT"]}

    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(access_control_service, "effective_access", effective_access)
    items = await access.verify_access(
        agent={"agent_id": "x"}, role_name="analyst", username="ana",
        encrypted_password="", session_id=None,
    )
    assert len(items) == 3 and running["peak"] == 1


@pytest.mark.asyncio
async def test_thread_list_hides_missing_agents_and_fails_on_other_errors(monkeypatch) -> None:
    async def require(agent_id: str, user: dict) -> dict:
        await asyncio.sleep(0.005)
        if agent_id == "gone":
            raise HTTPException(404, "Agent not found")
        if agent_id == "down":
            raise HTTPException(503, "Agent metadata is temporarily unavailable")
        return {"agent_id": agent_id}

    monkeypatch.setattr(router, "_require_agent", require)
    user = {"username": "ana", "active_role": "analyst"}
    assert await router._accessible_agent_ids(["sales", "gone", "ops"], user) == {"sales", "ops"}
    with pytest.raises(HTTPException) as failed:
        await router._accessible_agent_ids(["sales", "down", "gone"], user)
    assert failed.value.status_code == 503
