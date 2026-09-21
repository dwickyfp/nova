"""Unit coverage for Nova runtime service health signals."""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.modules.monitoring.runtime_health import RuntimeHealthProbe
from app.modules.task_orchestration.process_health import WorkerProcessHeartbeat


class FakeRedis:
    def __init__(
        self,
        *,
        ping: bool = True,
        scheduler_ttl: int = 20,
        workers: int = 1,
    ) -> None:
        self.ping_result = ping
        self.scheduler_ttl = scheduler_ttl
        self.workers = workers
        self.closed = False
        self.zadds: list[tuple[str, dict[str, float]]] = []
        self.zremranges: list[tuple[str, object, float]] = []
        self.zrems: list[tuple[str, str]] = []

    async def ping(self) -> bool:
        return self.ping_result

    async def ttl(self, key: str) -> int:
        assert key == settings.SCHEDULER_LEADER_LOCK_KEY
        return self.scheduler_ttl

    async def zcount(self, key: str, minimum: float, maximum: str) -> int:
        assert key == settings.WORKER_REGISTRY_KEY
        assert maximum == "+inf"
        assert minimum > 0
        return self.workers

    async def zadd(self, key: str, values: dict[str, float]) -> None:
        self.zadds.append((key, values))

    async def zremrangebyscore(self, key: str, minimum: object, maximum: float) -> None:
        self.zremranges.append((key, minimum, maximum))

    async def zrem(self, key: str, member: str) -> None:
        self.zrems.append((key, member))

    async def aclose(self) -> None:
        self.closed = True


def _probe(client: FakeRedis) -> RuntimeHealthProbe:
    return RuntimeHealthProbe(
        client_factory=lambda *_args, **_kwargs: client,
        clock=lambda: 1_800_000_000.0,
    )


class TestRuntimeHealthProbe:
    async def test_reports_all_services_healthy_from_live_signals(self):
        client = FakeRedis(scheduler_ttl=18, workers=2)

        result = await _probe(client).collect()

        assert result.redis.status == "healthy"
        assert result.scheduler.status == "healthy"
        assert result.worker.status == "healthy"
        assert result.worker.instances == 2
        assert client.closed is True

    async def test_missing_process_signals_are_unhealthy(self):
        result = await _probe(FakeRedis(scheduler_ttl=-2, workers=0)).collect()

        assert result.redis.status == "healthy"
        assert result.scheduler.status == "unhealthy"
        assert result.worker.status == "unhealthy"
        assert result.worker.instances == 0

    async def test_redis_failure_makes_dependent_process_state_unknown(self):
        result = await _probe(FakeRedis(ping=False)).collect()

        assert result.redis.status == "unhealthy"
        assert result.scheduler.status == "unknown"
        assert result.worker.status == "unknown"


class TestWorkerProcessHeartbeat:
    async def test_publishes_timestamp_and_removes_marker_on_close(self):
        client = FakeRedis()
        heartbeat = WorkerProcessHeartbeat(
            client,
            instance_id="worker-test",
            registry_key="test:workers",
            stale_seconds=30,
            clock=lambda: 1_000.0,
        )

        await heartbeat.publish()
        await heartbeat.close()

        assert client.zadds == [("test:workers", {"worker-test": 1_000.0})]
        assert client.zremranges == [("test:workers", "-inf", 700.0)]
        assert client.zrems == [("test:workers", "worker-test")]

    async def test_run_stops_without_waiting_for_the_full_interval(self):
        client = FakeRedis()
        heartbeat = WorkerProcessHeartbeat(
            client,
            instance_id="worker-test",
            interval_seconds=60,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat.run(stop))

        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

        assert client.zadds
