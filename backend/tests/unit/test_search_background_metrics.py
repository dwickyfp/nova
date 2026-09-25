"""Background AI Search failures and builds have bounded, useful metrics."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.modules.intelligence import search as module


class _Counter:
    def __init__(self) -> None:
        self.count = 0
        self.statuses: list[str] = []

    def labels(self, *, status: str):
        self.statuses.append(status)
        return self

    def inc(self) -> None:
        self.count += 1


class _Gauge:
    def __init__(self) -> None:
        self.value = 0

    def inc(self) -> None:
        self.value += 1

    def dec(self) -> None:
        self.value -= 1


class _Timestamp:
    def __init__(self) -> None:
        self.values: list[float] = []

    def set(self, value: float) -> None:
        self.values.append(value)


class _Histogram:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.durations: list[float] = []

    def labels(self, *, status: str):
        self.statuses.append(status)
        return self

    def observe(self, duration: float) -> None:
        self.durations.append(duration)


@pytest.mark.asyncio
async def test_search_poll_failure_is_counted_and_loop_continues(monkeypatch) -> None:
    errors = _Counter()
    timestamp = _Timestamp()
    monkeypatch.setattr(module, "SEARCH_POLL_ERRORS", errors)
    monkeypatch.setattr(module, "SEARCH_LAST_SUCCESSFUL_POLL", timestamp)
    monkeypatch.setattr(module.db, "execute_system", AsyncMock(side_effect=RuntimeError("offline")))

    async def stop_after_poll(seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", stop_after_poll)
    with pytest.raises(asyncio.CancelledError):
        await module.SearchService()._poll()

    assert errors.count == 1
    assert timestamp.values == []


@pytest.mark.asyncio
async def test_search_successful_queue_poll_updates_last_success_timestamp(monkeypatch) -> None:
    timestamp = _Timestamp()
    monkeypatch.setattr(module, "SEARCH_LAST_SUCCESSFUL_POLL", timestamp)
    monkeypatch.setattr(module.db, "execute_system", AsyncMock(return_value={"rows": []}))

    async def stop_after_poll(seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", stop_after_poll)
    with pytest.raises(asyncio.CancelledError):
        await module.SearchService()._poll()

    assert len(timestamp.values) == 1
    assert timestamp.values[0] > 0


@pytest.mark.asyncio
async def test_search_reconciliation_failure_counts_one_index(monkeypatch) -> None:
    errors = _Counter()
    monkeypatch.setattr(module, "SEARCH_RECONCILIATION_ERRORS", errors)
    monkeypatch.setattr(
        module.db,
        "execute_system",
        AsyncMock(return_value={"rows": [["private_index", 1]]}),
    )
    service = module.SearchService()
    monkeypatch.setattr(service, "_get", AsyncMock(side_effect=RuntimeError("offline")))

    await service._reconcile()

    assert errors.count == 1
    assert errors.statuses == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("built", "status"), [(True, "success"), (False, "failed")])
async def test_search_build_tracks_active_and_outcome(
    monkeypatch, built: bool, status: str
) -> None:
    active, errors, outcomes, duration = _Gauge(), _Counter(), _Counter(), _Histogram()
    monkeypatch.setattr(module, "SEARCH_BUILDS_ACTIVE", active)
    monkeypatch.setattr(module, "SEARCH_BUILD_ERRORS", errors)
    monkeypatch.setattr(module, "SEARCH_BUILDS", outcomes)
    monkeypatch.setattr(module, "SEARCH_BUILD_DURATION", duration)
    service = module.SearchService()

    @asynccontextmanager
    async def unlocked(name: str):
        yield

    async def build_locked(definition: dict, version: dict) -> bool:
        assert active.value == 1
        return built

    monkeypatch.setattr(service, "_lock", unlocked)
    monkeypatch.setattr(service, "_get", AsyncMock(return_value={"name": "private_index"}))
    monkeypatch.setattr(
        service,
        "_version",
        AsyncMock(return_value={"build_status": "PENDING"}),
    )
    monkeypatch.setattr(service, "_build_locked", build_locked)

    await service.build("private_index", 1)

    assert active.value == 0
    assert errors.count == (0 if built else 1)
    assert outcomes.count == 1
    assert outcomes.statuses == [status]
    assert duration.statuses == [status]
    assert len(duration.durations) == 1
    assert duration.durations[0] >= 0


@pytest.mark.asyncio
async def test_search_failed_build_body_returns_failed_after_persisting_state(monkeypatch) -> None:
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(module.db, "execute_system", execute)
    monkeypatch.setattr(
        module.vector_backend,
        "drop_index",
        AsyncMock(side_effect=RuntimeError("storage unavailable")),
    )
    definition = {"name": "private_index", "id": "opaque_id", "active_version": None}
    version = {
        "version": 1,
        "dimensions": 0,
        "metric": "cosine",
        "model_id": None,
        "build_status": "PENDING",
    }

    completed = await module.SearchService()._build_locked(definition, version)

    assert completed is False
    statements = [call.args[0] for call in execute.await_args_list]
    assert any("SET build_status='FAILED'" in sql for sql in statements)
    assert any("SET status='FAILED'" in sql for sql in statements)
