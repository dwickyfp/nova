"""API and worker updates must serialize against the same durable run row."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.redis import session_store
from app.modules.agents.harness_repository import AdmissionUnavailable, HarnessRepository
from app.modules.agents.run_journal import AgentRunJournal
from tests.unit.fake_lock_redis import FakeLockRedis


@pytest.mark.parametrize("operation", ["heartbeat", "finish"])
async def test_lost_pg_claim_cannot_restore_a_terminal_run(operation):
    from app.core.metadata_db import MetadataConflict
    from app.modules.access_control.security_context import SecurityContext
    from app.modules.agents.metadata_repository import AgentMetadataRepository

    connection = MagicMock(execute=AsyncMock())
    repository = AgentMetadataRepository(connection, SecurityContext("alice", "analyst"))
    repository._bound = AsyncMock()
    repository._claimed_run = AsyncMock(side_effect=MetadataConflict())
    if operation == "heartbeat":
        assert await repository.heartbeat_run("run", generation=1) is False
    else:
        assert await repository.finish_run("run", "completed", generation=1) is False
    repository._claimed_run.assert_awaited_once_with("run", 1)
    connection.execute.assert_not_awaited()


async def test_unbound_actor_prevents_a_journal_write(monkeypatch):
    from app.core import metadata_scope
    from app.core.metadata_db import MetadataPermissionDenied
    monkeypatch.setattr(metadata_scope, "acting_actor", lambda: None)
    with pytest.raises(MetadataPermissionDenied):
        await AgentRunJournal().heartbeat("run", generation=1)


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

