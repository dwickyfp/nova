"""Migration worker metrics reflect claimed work and bounded outcomes."""

import asyncio
from typing import Any

import pytest

from app.modules.migration import job_worker as module


class _Gauge:
    def __init__(self) -> None:
        self.value = 0

    def inc(self) -> None:
        self.value += 1

    def dec(self) -> None:
        self.value -= 1


class _LabeledMetric:
    def __init__(self) -> None:
        self.increments: list[dict[str, str]] = []
        self.observations: list[tuple[dict[str, str], float]] = []
        self._labels: dict[str, str] = {}

    def labels(self, **labels: str) -> "_LabeledMetric":
        self._labels = labels
        return self

    def inc(self) -> None:
        self.increments.append(dict(self._labels))

    def observe(self, value: float) -> None:
        self.observations.append((dict(self._labels), value))


@pytest.fixture
def metrics(monkeypatch):
    gauge = _Gauge()
    jobs = _LabeledMetric()
    duration = _LabeledMetric()
    errors = _LabeledMetric()
    monkeypatch.setattr(module, "MIGRATION_WORKER_ACTIVE_JOBS", gauge)
    monkeypatch.setattr(module, "MIGRATION_WORKER_JOBS", jobs)
    monkeypatch.setattr(module, "MIGRATION_WORKER_JOB_DURATION", duration)
    monkeypatch.setattr(module, "MIGRATION_WORKER_POLL_ERRORS", errors)
    return gauge, jobs, duration, errors


@pytest.fixture
def claimed_job(monkeypatch):
    class Repository:
        def __init__(self) -> None:
            self.finished: list[str] = []

        async def finish(self, job_id: str, *, status: str,
                         result: dict[str, Any] | None = None,
                         error_code: str | None = None) -> None:
            self.finished.append(status)

    repository = Repository()
    monkeypatch.setattr(module, "migration_job_repo", repository)

    async def session(self, job):
        return "private-session", {"encrypted_password": "private-password"}

    async def heartbeat(self, job_id, session_id, stop_event):
        await stop_event.wait()

    async def audit(self, job, status):
        return None

    async def clear(client, job_id):
        return None

    monkeypatch.setattr(module.MigrationJobWorker, "_session", session)
    monkeypatch.setattr(module.MigrationJobWorker, "_heartbeat", heartbeat)
    monkeypatch.setattr(module.MigrationJobWorker, "_audit_safely", audit)
    monkeypatch.setattr(module, "clear_job_session", clear)
    return repository, {"id": "private-job-id", "operation": "databases", "request_json": "{}"}


@pytest.mark.asyncio
async def test_active_job_and_success_duration_return_to_zero(
    monkeypatch, metrics, claimed_job
) -> None:
    gauge, jobs, duration, _ = metrics
    repository, job = claimed_job
    started = asyncio.Event()
    release = asyncio.Event()

    async def read(self, job, payload, session_id, session):
        started.set()
        await release.wait()
        return {"databases": []}

    monkeypatch.setattr(module.MigrationJobWorker, "_read", read)
    worker = module.MigrationJobWorker(None)
    task = asyncio.create_task(worker.process_claimed(job))
    await started.wait()
    assert gauge.value == 1
    release.set()
    await task

    assert gauge.value == 0
    assert repository.finished == ["succeeded"]
    assert jobs.increments == [{"operation": "databases", "status": "succeeded"}]
    assert len(duration.observations) == 1
    assert duration.observations[0][0] == jobs.increments[0]
    assert duration.observations[0][1] >= 0
    assert "private-job-id" not in str(jobs.increments)
    assert "private-session" not in str(jobs.increments)


@pytest.mark.asyncio
async def test_cancellation_records_interrupted_and_decrements_active(
    monkeypatch, metrics, claimed_job
) -> None:
    gauge, jobs, _, _ = metrics
    repository, job = claimed_job
    started = asyncio.Event()

    async def read(self, job, payload, session_id, session):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module.MigrationJobWorker, "_read", read)
    worker = module.MigrationJobWorker(None)
    task = asyncio.create_task(worker.process_claimed(job))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert repository.finished == ["interrupted"]
    assert jobs.increments == [{"operation": "databases", "status": "interrupted"}]
    assert gauge.value == 0


@pytest.mark.asyncio
async def test_unknown_operation_is_collapsed_to_other(monkeypatch, metrics, claimed_job) -> None:
    gauge, jobs, _, _ = metrics
    repository, job = claimed_job
    job["operation"] = "source-private-123"

    async def read(self, job, payload, session_id, session):
        raise ValueError("unsupported operation")

    monkeypatch.setattr(module.MigrationJobWorker, "_read", read)
    await module.MigrationJobWorker(None).process_claimed(job)

    assert repository.finished == ["failed"]
    assert jobs.increments == [{"operation": "other", "status": "failed"}]
    assert gauge.value == 0


@pytest.mark.asyncio
async def test_terminal_write_failure_is_not_reported_as_completed(
    monkeypatch, metrics, claimed_job
) -> None:
    gauge, jobs, duration, _ = metrics
    repository, job = claimed_job

    async def read(self, job, payload, session_id, session):
        return {"databases": []}

    async def fail_finish(job_id, *, status, result=None, error_code=None):
        raise RuntimeError("terminal update unavailable")

    monkeypatch.setattr(module.MigrationJobWorker, "_read", read)
    monkeypatch.setattr(repository, "finish", fail_finish)
    with pytest.raises(RuntimeError, match="terminal update unavailable"):
        await module.MigrationJobWorker(None).process_claimed(job)

    assert jobs.increments == [{"operation": "databases", "status": "unfinished"}]
    assert duration.observations[0][0] == jobs.increments[0]
    assert gauge.value == 0


@pytest.mark.asyncio
async def test_poll_and_claim_exceptions_have_distinct_bounded_phases(monkeypatch, metrics) -> None:
    _, _, _, errors = metrics

    class Repository:
        fail_poll = True

        async def queued(self, *, limit: int):
            if self.fail_poll:
                raise RuntimeError("poll failed")
            return [{"id": "private-job-id", "operation": "engine"}]

        async def claim(self, job_id: str):
            raise RuntimeError("claim failed")

    repository = Repository()
    monkeypatch.setattr(module, "migration_job_repo", repository)
    worker = module.MigrationJobWorker(None)
    with pytest.raises(RuntimeError, match="poll failed"):
        await worker.run_once()
    repository.fail_poll = False
    with pytest.raises(RuntimeError, match="claim failed"):
        await worker.run_once()

    assert errors.increments == [{"phase": "poll"}, {"phase": "claim"}]
