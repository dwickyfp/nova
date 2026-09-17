"""Leader-lock and scheduler-service tests (NOVA-35, criterion 7).

Two scheduler instances must not both enqueue. The lock is the guarantee, so it
is tested both against a fake Redis (fast, deterministic, for the compare-and-set
paths) and — in the integration suite — against a real Redis.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.modules.task_orchestration.service import SchedulerService
from app.modules.task_orchestration.transport import LeaderLock

_RELEASE_MARKER = 'redis.call("del"'
_RENEW_MARKER = 'redis.call("expire"'


class FakeRedis:
    """Minimal async Redis with the two compare-and-set Lua scripts inlined."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    async def eval(self, script: str, numkeys: int, key: str, *args: Any):
        current = self.store.get(key)
        if _RELEASE_MARKER in script:
            if current is not None and current == args[0]:
                del self.store[key]
                return 1
            return 0
        if _RENEW_MARKER in script:
            if current is not None and current == args[0]:
                self.expiries[key] = int(args[1])
                return 1
            return 0
        raise AssertionError(f"unexpected script: {script}")


class RecordingTick:
    def __init__(self) -> None:
        self.tick_count = 0

    async def tick(self, now: datetime | None = None):
        self.tick_count += 1
        return None


class TestLeaderLock:
    async def test_first_acquire_wins(self):
        lock = LeaderLock(FakeRedis(), key="k", ttl_seconds=30)
        assert await lock.acquire() is True
        assert lock.is_leader is True

    async def test_second_acquire_fails_while_held(self):
        client = FakeRedis()
        first = LeaderLock(client, key="k", ttl_seconds=30)
        second = LeaderLock(client, key="k", ttl_seconds=30)
        assert await first.acquire() is True
        assert await second.acquire() is False
        assert second.is_leader is False
        assert first.is_leader is True

    async def test_release_frees_the_lock_for_the_other_instance(self):
        client = FakeRedis()
        first = LeaderLock(client, key="k", ttl_seconds=30)
        second = LeaderLock(client, key="k", ttl_seconds=30)
        assert await first.acquire() is True
        await first.release()
        assert await second.acquire() is True

    async def test_release_does_not_delete_a_successors_lock(self):
        client = FakeRedis()
        stale = LeaderLock(client, key="k", ttl_seconds=30)
        await stale.acquire()
        # Simulate TTL expiry then a successor taking over.
        client.store["k"] = "successor-token"
        await stale.release()
        assert client.store["k"] == "successor-token"

    async def test_renew_fails_and_drops_leadership_when_lock_lost(self):
        client = FakeRedis()
        lock = LeaderLock(client, key="k", ttl_seconds=30)
        await lock.acquire()
        client.store["k"] = "someone-else"
        assert await lock.renew() is False
        assert lock.is_leader is False

    async def test_renew_extends_ttl_while_held(self):
        client = FakeRedis()
        lock = LeaderLock(client, key="k", ttl_seconds=30)
        await lock.acquire()
        client.expiries["k"] = 1
        assert await lock.renew() is True
        assert client.expiries["k"] == 30


class TestSchedulerServiceSingleton:
    async def test_only_the_leader_runs_a_tick(self):
        client = FakeRedis()
        tick_a = RecordingTick()
        tick_b = RecordingTick()
        service_a = SchedulerService(tick_a, LeaderLock(client, key="k", ttl_seconds=30))
        service_b = SchedulerService(tick_b, LeaderLock(client, key="k", ttl_seconds=30))

        assert await service_a.run_once() is True
        assert await service_b.run_once() is False

        assert tick_a.tick_count == 1
        assert tick_b.tick_count == 0

    async def test_leadership_handover_after_release(self):
        client = FakeRedis()
        tick_a = RecordingTick()
        tick_b = RecordingTick()
        lock_a = LeaderLock(client, key="k", ttl_seconds=30)
        service_a = SchedulerService(tick_a, lock_a)
        service_b = SchedulerService(tick_b, LeaderLock(client, key="k", ttl_seconds=30))

        await service_a.run_once()
        await lock_a.release()
        assert await service_b.run_once() is True
        assert tick_b.tick_count == 1

    async def test_run_forever_stops_on_the_stop_event(self):
        client = FakeRedis()
        tick = RecordingTick()
        service = SchedulerService(
            tick, LeaderLock(client, key="k", ttl_seconds=30), poll_interval=0.01
        )
        stop = asyncio.Event()

        async def stop_soon() -> None:
            await asyncio.sleep(0.03)
            stop.set()

        await asyncio.gather(service.run_forever(stop), stop_soon())
        assert tick.tick_count >= 1
        assert "k" not in client.store


class TestRunOnceDoesNotCrashOnTickError:
    async def test_tick_exception_is_the_callers_to_handle(self):
        class BoomTick:
            async def tick(self, now: datetime | None = None):
                raise RuntimeError("boom")

        service = SchedulerService(
            BoomTick(), LeaderLock(FakeRedis(), key="k", ttl_seconds=30)
        )
        with pytest.raises(RuntimeError):
            await service.run_once()


DUE = datetime(2026, 1, 1, tzinfo=UTC)
