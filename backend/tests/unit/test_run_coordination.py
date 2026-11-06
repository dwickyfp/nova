"""API and worker updates must serialize against the same durable run row."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.redis import session_store
from app.modules.agents import run_coordination as module
from app.modules.agents.harness_repository import AdmissionUnavailable, HarnessRepository
from app.modules.agents.run_journal import AgentRunJournal
from tests.unit.fake_lock_redis import FakeLockRedis


async def test_shared_lock_prevents_a_heartbeat_from_restoring_cancelled_status(monkeypatch):
    redis = FakeLockRedis()
    monkeypatch.setattr(session_store, "_redis", redis)
    entered = asyncio.Event()
    release = asyncio.Event()
    status = "running"
    writes = []

    async def execute(sql, params):
        nonlocal status
        snapshot = status
        writes.append(sql)
        if len(writes) == 1:
            entered.set()
            await release.wait()
        if "status = %s" in sql:
            status = params[0]
        elif snapshot == "running":
            status = snapshot
        return {"affected": 1}

    monkeypatch.setattr(module.db, "execute_system", execute)
    heartbeat = asyncio.create_task(module.update_run(
        "run", "UPDATE NOVA_SYSTEM.CONFIG_AGENT_RUNS SET updated_at = NOW() WHERE run_id = %s",
        ["run"],
    ))
    await entered.wait()
    cancel = asyncio.create_task(AgentRunJournal().finish("run", "cancelled"))
    await asyncio.sleep(0)
    assert len(writes) == 1
    release.set()
    await asyncio.gather(heartbeat, cancel)
    assert status == "cancelled"
    assert list(redis.locks) == ["nova:agent:run-write:run"]
    assert not await redis.locks["nova:agent:run-write:run"].owned()


async def test_missing_coordinator_prevents_any_write(monkeypatch):
    monkeypatch.setattr(session_store, "_redis", None)
    write = AsyncMock()
    monkeypatch.setattr(module.db, "execute_system", write)
    with pytest.raises(RuntimeError, match="coordinator is unavailable"):
        await module.update_run("run", "UPDATE", [])
    write.assert_not_awaited()


async def test_lost_lock_after_write_is_uncertain_and_never_replayed(monkeypatch):
    redis = FakeLockRedis()
    monkeypatch.setattr(session_store, "_redis", redis)
    write = AsyncMock(return_value={"affected": 1})
    monkeypatch.setattr(module.db, "execute_system", write)
    lock = redis.lock("nova:agent:run-write:run", timeout=600)
    lock.owned = AsyncMock(side_effect=[True, False])
    with pytest.raises(RuntimeError, match="lock was lost"):
        await module.update_run("run", "UPDATE", [])
    write.assert_awaited_once()


async def test_thread_admission_serializes_the_same_owner_thread(monkeypatch):
    monkeypatch.setattr(session_store, "_redis", FakeLockRedis())
    repository = HarnessRepository()
    order = []

    async def admitted(name: str) -> None:
        async with repository.admission_lock("thread", "alice") as assert_owned:
            order.append(f"{name}:in")
            await asyncio.sleep(0)
            await assert_owned()
            order.append(f"{name}:out")

    await asyncio.gather(admitted("first"), admitted("second"))
    assert order == ["first:in", "first:out", "second:in", "second:out"]


async def test_thread_admission_fails_closed_without_redis(monkeypatch):
    monkeypatch.setattr(session_store, "_redis", None)
    with pytest.raises(AdmissionUnavailable):
        async with HarnessRepository().admission_lock("thread", "alice"):
            pass

