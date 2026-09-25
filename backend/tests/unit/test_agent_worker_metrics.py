"""Metrics for claimed Studio Auto runs stay accurate across process outcomes."""

import asyncio

import pytest

from app.modules.agents import harness_worker as module


class _Gauge:
    def __init__(self) -> None:
        self.value = 0

    def inc(self) -> None:
        self.value += 1

    def dec(self) -> None:
        self.value -= 1


class _Counter:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def labels(self, *, status: str):
        self.statuses.append(status)
        return self

    def inc(self) -> None:
        pass


class _Histogram:
    def __init__(self) -> None:
        self.durations: list[float] = []
        self.statuses: list[str] = []

    def labels(self, *, status: str):
        self.statuses.append(status)
        return self

    def observe(self, value: float) -> None:
        self.durations.append(value)


class _PhaseCounter:
    def __init__(self) -> None:
        self.phases: list[str] = []

    def labels(self, *, phase: str):
        self.phases.append(phase)
        return self

    def inc(self) -> None:
        pass


@pytest.mark.asyncio
async def test_agent_worker_metrics_only_track_claimed_runs(monkeypatch) -> None:
    gauge, counter, histogram = _Gauge(), _Counter(), _Histogram()
    monkeypatch.setattr(module, "AGENT_WORKER_ACTIVE", gauge)
    monkeypatch.setattr(module, "AGENT_WORKER_RUNS", counter)
    monkeypatch.setattr(module, "AGENT_WORKER_RUN_DURATION", histogram)

    class Repository:
        status = "running"

        async def claim(self, run_id: str, lease_id: str) -> bool:
            return run_id != "already-owned"

        async def get(self, run_id: str) -> dict[str, str]:
            return {"status": self.status}

    repository = Repository()
    worker = module.AgentHarnessWorker(repository)
    started = asyncio.Event()
    release = asyncio.Event()

    async def process_claimed(run_id: str, lease_id: str) -> None:
        started.set()
        await release.wait()
        repository.status = "waiting_for_agent"

    monkeypatch.setattr(worker, "_process_claimed", process_claimed)
    await worker.process("already-owned", "worker")
    assert gauge.value == 0
    assert counter.statuses == []

    task = asyncio.create_task(worker.process("claimed", "worker"))
    await started.wait()
    assert gauge.value == 1
    release.set()
    await task

    assert gauge.value == 0
    assert counter.statuses == ["waiting"]
    assert histogram.statuses == ["waiting"]
    assert len(histogram.durations) == 1
    assert histogram.durations[0] >= 0


@pytest.mark.asyncio
async def test_agent_worker_metrics_clear_active_on_failure(monkeypatch) -> None:
    gauge, counter, histogram = _Gauge(), _Counter(), _Histogram()
    monkeypatch.setattr(module, "AGENT_WORKER_ACTIVE", gauge)
    monkeypatch.setattr(module, "AGENT_WORKER_RUNS", counter)
    monkeypatch.setattr(module, "AGENT_WORKER_RUN_DURATION", histogram)

    class Repository:
        async def claim(self, run_id: str, lease_id: str) -> bool:
            return True

    worker = module.AgentHarnessWorker(Repository())

    async def process_claimed(run_id: str, lease_id: str) -> None:
        raise RuntimeError("processing failed")

    monkeypatch.setattr(worker, "_process_claimed", process_claimed)
    with pytest.raises(RuntimeError, match="processing failed"):
        await worker.process("run", "worker")

    assert gauge.value == 0
    assert counter.statuses == ["failed"]
    assert histogram.statuses == ["failed"]
    assert len(histogram.durations) == 1


@pytest.mark.asyncio
async def test_agent_worker_heartbeat_records_completed_poll(monkeypatch) -> None:
    stop = asyncio.Event()
    pulses: list[str] = []
    monkeypatch.setattr(module, "heartbeat", pulses.append)

    class Repository:
        async def recover_stale(self) -> None:
            pass

        async def expire_waiting(self, *, max_age_seconds: int) -> None:
            pass

        async def queued(self, *, limit: int) -> list[dict]:
            stop.set()
            return []

    worker = module.AgentHarnessWorker(Repository())
    await worker.run_forever(stop, "worker")
    assert pulses == ["agent-worker"]


@pytest.mark.asyncio
async def test_agent_worker_claim_failure_is_counted_without_active_run(monkeypatch) -> None:
    errors = _PhaseCounter()
    active = _Gauge()
    monkeypatch.setattr(module, "AGENT_WORKER_POLL_ERRORS", errors)
    monkeypatch.setattr(module, "AGENT_WORKER_ACTIVE", active)

    class Repository:
        async def claim(self, run_id: str, lease_id: str) -> bool:
            raise RuntimeError("claim unavailable")

    worker = module.AgentHarnessWorker(Repository())
    with pytest.raises(RuntimeError, match="claim unavailable"):
        await worker.process("run", "worker")

    assert errors.phases == ["claim"]
    assert active.value == 0


@pytest.mark.asyncio
async def test_agent_worker_poll_failure_has_no_success_heartbeat(monkeypatch) -> None:
    errors = _PhaseCounter()
    pulses: list[str] = []
    monkeypatch.setattr(module, "AGENT_WORKER_POLL_ERRORS", errors)
    monkeypatch.setattr(module, "heartbeat", pulses.append)

    class Repository:
        async def recover_stale(self) -> None:
            raise RuntimeError("poll unavailable")

    worker = module.AgentHarnessWorker(Repository())
    with pytest.raises(RuntimeError, match="poll unavailable"):
        await worker.run_forever(asyncio.Event(), "worker")

    assert errors.phases == ["poll"]
    assert pulses == []
