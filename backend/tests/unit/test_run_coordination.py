"""API and worker updates must serialize against the same durable run row."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.redis import session_store
from app.modules.agents import run_coordination as module
from app.modules.agents.harness_repository import HarnessRepository
from app.modules.agents.run_journal import AgentRunJournal
from tests.unit.test_agent_harness import _EventRedis


async def test_shared_lock_prevents_a_heartbeat_from_restoring_cancelled_status(monkeypatch):
    redis = _EventRedis()
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
    heartbeat = asyncio.create_task(HarnessRepository().heartbeat("run", "worker"))
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
    redis = _EventRedis()
    monkeypatch.setattr(session_store, "_redis", redis)
    write = AsyncMock(return_value={"affected": 1})
    monkeypatch.setattr(module.db, "execute_system", write)
    lock = redis.lock("nova:agent:run-write:run", timeout=600)
    lock.owned = AsyncMock(side_effect=[True, False])
    with pytest.raises(RuntimeError, match="lock was lost"):
        await module.update_run("run", "UPDATE", [])
    write.assert_awaited_once()
