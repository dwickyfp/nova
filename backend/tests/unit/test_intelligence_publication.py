"""Publication repairs each partial write without publishing a stale candidate."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.intelligence import semantic_views

USER = {
    "username": "owner",
    "active_role": "ANALYST",
    "encrypted_password": "opaque",
    "session_id": "caller-session",
    "security_context_version": 7,
    "roles": ["ANALYST"],
}


@pytest.fixture
def publication_preflight(monkeypatch):
    from app.core.redis import session_store
    from app.modules.intelligence import engine_repository

    service = semantic_views.SemanticViewService()
    view = {"name": "sales", "database_name": "sales_db", "active_version": 1}
    version = {
        "status": "VALIDATED",
        "fingerprint": "candidate",
        "definition": {},
        "validation": {"valid": True, "baseline_version": 1, "regression": {"changed": 0}},
    }
    inside_lock = False
    source_checked = False
    lease = SimpleNamespace(renew=AsyncMock(return_value=True))

    @asynccontextmanager
    async def lock(_key):
        nonlocal inside_lock
        assert source_checked
        inside_lock = True
        try:
            yield lease
        finally:
            inside_lock = False

    async def probe(_definition, caller):
        nonlocal source_checked
        assert not inside_lock and caller is USER
        source_checked = True
        return True

    async def write(_sql, _params):
        assert inside_lock
        return {"affected": 1, "rows": []}

    writes = AsyncMock(side_effect=write)
    checkpoint = AsyncMock(return_value=SimpleNamespace(error=None))
    monkeypatch.setattr(engine_repository, "metadata_lock", lock)
    monkeypatch.setattr(service, "_owned", AsyncMock(side_effect=lambda *_: deepcopy(view)))
    monkeypatch.setattr(service, "_version", AsyncMock(side_effect=lambda *_: deepcopy(version)))
    monkeypatch.setattr(service, "_source_access", probe)
    monkeypatch.setattr(service, "describe", AsyncMock(return_value={"active_version": 2}))
    monkeypatch.setattr(service, "_audit", AsyncMock())
    monkeypatch.setattr(semantic_views.query_service, "execute", checkpoint)
    monkeypatch.setattr(semantic_views.db, "execute_system", writes)
    monkeypatch.setattr(session_store, "get", AsyncMock(return_value=deepcopy(USER)))
    return service, view, version, writes, checkpoint, lease


async def test_publication_probes_sources_before_acquiring_the_short_write_lease(
    publication_preflight,
):
    service, _, _, writes, checkpoint, _ = publication_preflight
    assert await service.publish("view", 2, USER) == {"active_version": 2}
    assert writes.await_count == 3
    checkpoint.assert_awaited_once_with(
        sql="SELECT 1",
        username="owner",
        encrypted_password="opaque",
        database="sales_db",
        role="ANALYST",
        session_id="caller-session",
        security_context_version=7,
        max_rows=0,
    )


@pytest.mark.parametrize("changed", ["definition", "fingerprint", "validation", "baseline"])
async def test_publication_rejects_inputs_changed_during_source_probes(
    publication_preflight, changed
):
    service, view, version, writes, checkpoint, _ = publication_preflight

    async def change_after_checks(**_kwargs):
        if changed == "baseline":
            view["active_version"] = 3
        elif changed == "validation":
            version["validation"]["regression"]["changed"] = 1
        elif changed == "definition":
            version["definition"]["changed"] = True
        else:
            version["fingerprint"] = "replacement"
        return SimpleNamespace(error=None)

    checkpoint.side_effect = change_after_checks
    with pytest.raises(HTTPException) as error:
        await service.publish("view", 2, USER, acknowledge_regressions=True)
    assert error.value.status_code == 409
    writes.assert_not_awaited()


async def test_publication_denies_expired_execution_context_before_any_write(publication_preflight):
    service, _, _, writes, checkpoint, lease = publication_preflight
    checkpoint.return_value = SimpleNamespace(error="caller context revoked")
    with pytest.raises(HTTPException) as error:
        await service.publish("view", 2, USER)
    assert error.value.status_code == 403
    writes.assert_not_awaited()
    lease.renew.assert_not_awaited()


async def test_publication_still_fences_writes_after_successful_preflight(publication_preflight):
    service, _, _, writes, _, lease = publication_preflight
    lease.renew.return_value = False
    with pytest.raises(HTTPException) as error:
        await service.publish("view", 2, USER)
    assert error.value.status_code == 409
    writes.assert_not_awaited()


@pytest.mark.parametrize(
    "changed",
    [
        None,
        {"active_role": "OTHER", "roles": ["OTHER"]},
        {"security_context_version": 8},
        {"username": "other"},
    ],
)
async def test_publication_revalidates_session_inside_write_lease(
    publication_preflight, monkeypatch, changed
):
    from app.core.redis import session_store

    service, _, _, writes, _, lease = publication_preflight
    monkeypatch.setattr(
        session_store, "get", AsyncMock(return_value={**USER, **changed} if changed else None)
    )
    with pytest.raises(HTTPException) as error:
        await service.publish("view", 2, USER)
    assert error.value.status_code == 403
    writes.assert_not_awaited()
    lease.renew.assert_not_awaited()


@pytest.mark.parametrize("failed_write", [1, 2, 3])
async def test_publication_recovers_each_write_boundary(monkeypatch, failed_write):
    service = semantic_views.SemanticViewService()
    view = {"name": "sales", "active_version": 1}
    versions = {
        1: {"status": "ACTIVE"},
        2: {
            "status": "VALIDATED",
            "definition": {},
            "validation": {"valid": True, "baseline_version": 1, "regression": {"changed": 0}},
        },
    }
    monkeypatch.setattr(service, "_owned", AsyncMock(side_effect=lambda *_: deepcopy(view)))
    monkeypatch.setattr(
        service, "_version", AsyncMock(side_effect=lambda _, n: deepcopy(versions[n]))
    )
    monkeypatch.setattr(service, "describe", AsyncMock(side_effect=lambda *_: deepcopy(view)))
    monkeypatch.setattr(service, "_source_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_audit", AsyncMock())
    count = 0

    async def execute(sql, params):
        nonlocal count
        count += 1
        if count == failed_write:
            raise RuntimeError("process stopped at publication boundary")
        if "SET status='ACTIVE'" in sql:
            versions[2]["status"] = "ACTIVE"
        elif "SET active_version=" in sql:
            assert versions[2]["status"] == "ACTIVE"
            assert versions[1]["status"] == "ACTIVE"
            view["active_version"] = 2
        elif "SET status='DEPRECATED'" in sql:
            assert view["active_version"] == 2
            versions[1]["status"] = "DEPRECATED"
        return {"affected": 1, "rows": []}

    monkeypatch.setattr(semantic_views.db, "execute_system", execute)
    lease = type("Lease", (), {"renew": AsyncMock(return_value=True)})()
    with pytest.raises(RuntimeError, match="boundary"):
        await service._publish_locked("view", 2, {"username": "owner"}, False, lease)
    assert versions[view["active_version"]]["status"] == "ACTIVE"
    result = await service._publish_locked("view", 2, {"username": "owner"}, False, lease)
    assert result["active_version"] == 2
    assert versions[1]["status"] == "DEPRECATED"


async def test_recovery_cannot_replace_a_newer_publication(monkeypatch):
    service = semantic_views.SemanticViewService()
    monkeypatch.setattr(
        service, "_owned", AsyncMock(return_value={"name": "sales", "active_version": 3})
    )
    monkeypatch.setattr(
        service,
        "_version",
        AsyncMock(
            return_value={"status": "ACTIVE", "validation": {"valid": True, "baseline_version": 1}}
        ),
    )
    write = AsyncMock()
    monkeypatch.setattr(semantic_views.db, "execute_system", write)
    with pytest.raises(HTTPException) as failure:
        await service._publish_locked("view", 2, {}, False, None)
    assert failure.value.status_code == 409
    write.assert_not_awaited()
