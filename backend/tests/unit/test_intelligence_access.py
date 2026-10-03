"""Intelligence native compatibility must not become a Ranger failure fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.agents import access
from app.modules.query.service import query_service


async def test_native_verification_delegates_principal_role_and_context_version(monkeypatch):
    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    monkeypatch.setattr(
        access, "resolve_agent_dependencies", AsyncMock(return_value=[("table", "sales.orders")])
    )
    execute = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(query_service, "execute", execute)
    result = await access.verify_access(
        agent={},
        role_name="FINANCE",
        username="alice",
        encrypted_password="cipher",
        session_id="session",
        security_context_version=7,
    )
    assert result[0].granted
    assert execute.await_args.kwargs == dict(
        sql="SELECT * FROM `sales`.`orders` LIMIT 1",
        username="alice",
        encrypted_password="cipher",
        database="sales",
        role="FINANCE",
        session_id="session",
        security_context_version=7,
        max_rows=1,
    )
    execute.return_value.error = "denied"
    assert not (
        await access.verify_access(
            agent={},
            role_name="FINANCE",
            username="alice",
            encrypted_password="cipher",
            session_id="session",
        )
    )[0].granted


async def test_ranger_outage_never_uses_native_grants(monkeypatch):
    from app.modules.access_control.service import access_control_service

    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    monkeypatch.setattr(
        access, "resolve_agent_dependencies", AsyncMock(return_value=[("table", "sales.orders")])
    )
    native = AsyncMock()
    monkeypatch.setattr(query_service, "execute", native)
    monkeypatch.setattr(
        access_control_service, "effective_access", AsyncMock(side_effect=RuntimeError("offline"))
    )
    with pytest.raises(RuntimeError, match="offline"):
        await access.verify_access(
            agent={},
            role_name="FINANCE",
            username="alice",
            encrypted_password="cipher",
            session_id="session",
        )
    native.assert_not_awaited()


async def test_semantic_probes_batch_without_sharing_delegated_connections(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.modules.intelligence.semantic_views import SemanticViewService
    from app.modules.query.service import delegated_connection

    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    active, peak = 0, 0
    calls = []

    async def probe(**kwargs):
        nonlocal active, peak
        calls.append(kwargs)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return SimpleNamespace(error=None)

    monkeypatch.setattr(query_service, "execute", probe)
    definition = {"datasets": [{"source": f"sales.table_{index}"} for index in range(23)]}
    user = {
        "username": "reader",
        "encrypted_password": "opaque",
        "active_role": "ANALYST",
        "session_id": "current",
        "security_context_version": 7,
    }
    with delegated_connection("reader", object()):
        assert await SemanticViewService._source_access(definition, user)
    assert peak == 1 and len(calls) == 23
    assert all(
        call["security_context_version"] == 7 and call["session_id"] == "current" for call in calls
    )
    assert all(
        call["sql"].startswith("SELECT * FROM") and call["sql"].endswith("LIMIT 1")
        for call in calls
    )
    assert all(call["max_rows"] == 0 for call in calls)
    calls.clear()
    assert await SemanticViewService._source_access(definition, user)
    assert peak == 3


async def test_semantic_source_timeout_cancels_work_and_never_grants_access(monkeypatch):
    import asyncio

    from app.modules.intelligence.semantic_views import SemanticViewService

    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    cancelled = asyncio.Event()
    original_wait = asyncio.wait_for

    async def blocked(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def short_deadline(awaitable, *, timeout):
        assert 0 < timeout <= 120
        return await original_wait(awaitable, timeout=0.01)

    monkeypatch.setattr(query_service, "execute", blocked)
    monkeypatch.setattr(asyncio, "wait_for", short_deadline)
    assert not await SemanticViewService._source_access(
        {"datasets": [{"source": "sales.orders"}]},
        {"username": "reader", "encrypted_password": "opaque", "active_role": "ANALYST"},
    )
    assert cancelled.is_set()


async def test_semantic_source_cycle_expiry_discards_successful_probes(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    from app.modules.intelligence.semantic_views import SemanticViewService

    monkeypatch.setattr(settings, "RANGER_ENABLED", False)

    @asynccontextmanager
    async def expired(seconds):
        assert seconds == 120
        yield
        raise TimeoutError()

    monkeypatch.setattr(asyncio, "timeout", expired)
    execute = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(query_service, "execute", execute)
    assert not await SemanticViewService._source_access(
        {"datasets": [{"source": "sales.orders"}]},
        {"username": "reader", "encrypted_password": "opaque", "active_role": "ANALYST"},
    )
    execute.assert_awaited_once()


@pytest.mark.parametrize("permission", [[], ["USAGE"], ["EXECUTE"]])
async def test_ranger_semantic_metadata_requires_current_select_policy(monkeypatch, permission):
    from app.modules.access_control.service import access_control_service
    from app.modules.intelligence.semantic_views import SemanticViewService

    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    policies = AsyncMock(return_value={"object_access": permission})
    probe = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(access_control_service, "effective_access", policies)
    monkeypatch.setattr(query_service, "execute", probe)
    user = {
        "username": "reader",
        "encrypted_password": "opaque",
        "active_role": "ANALYST",
        "session_id": "current",
        "security_context_version": 7,
    }
    assert not await SemanticViewService._source_access(
        {"datasets": [{"source": "sales.orders"}]}, user
    )
    policies.assert_awaited_once_with(
        principal="reader", active_role="ANALYST", resource="sales.orders"
    )
    probe.assert_not_awaited()


@pytest.mark.parametrize("failure", ["no_failure", "policy_outage", "engine_denied"])
async def test_ranger_semantic_probe_uses_real_select_under_current_identity(monkeypatch, failure):
    from app.modules.access_control.service import access_control_service
    from app.modules.intelligence.semantic_views import SemanticViewService

    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    policies = AsyncMock(return_value={"object_access": ["SELECT"]})
    probe = AsyncMock(return_value=SimpleNamespace(error=None))
    if failure == "policy_outage":
        policies.side_effect = RuntimeError("unavailable")
    if failure == "engine_denied":
        probe.side_effect = RuntimeError("denied")
    monkeypatch.setattr(access_control_service, "effective_access", policies)
    monkeypatch.setattr(query_service, "execute", probe)
    user = {
        "username": "reader",
        "encrypted_password": "opaque",
        "active_role": "ANALYST",
        "session_id": "current",
        "security_context_version": 7,
    }
    assert await SemanticViewService._source_access(
        {"datasets": [{"source": "sales.orders"}]}, user
    ) is (failure == "no_failure")
    if failure == "policy_outage":
        probe.assert_not_awaited()
    else:
        probe.assert_awaited_once_with(
            sql="SELECT * FROM `sales`.`orders` LIMIT 1",
            username="reader",
            encrypted_password="opaque",
            database="sales",
            role="ANALYST",
            session_id="current",
            security_context_version=7,
            max_rows=0,
        )
