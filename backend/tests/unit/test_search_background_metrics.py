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


@pytest.fixture
def metadata(monkeypatch):
    repo = AsyncMock()
    repo.pending_builds.return_value = []

    @asynccontextmanager
    async def scope(actor, *, read_only=False):
        assert actor.principal_type == "SERVICE"
        yield repo

    monkeypatch.setattr(module, "intelligence_metadata", scope)
    return repo


@pytest.mark.asyncio
async def test_search_poll_failure_is_counted_and_loop_continues(monkeypatch, metadata) -> None:
    errors = _Counter()
    timestamp = _Timestamp()
    monkeypatch.setattr(module, "SEARCH_POLL_ERRORS", errors)
    monkeypatch.setattr(module, "SEARCH_LAST_SUCCESSFUL_POLL", timestamp)
    metadata.pending_builds.side_effect = RuntimeError("offline")

    async def stop_after_poll(seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", stop_after_poll)
    with pytest.raises(asyncio.CancelledError):
        await module.SearchService()._poll()

    assert errors.count == 1
    assert timestamp.values == []


@pytest.mark.asyncio
async def test_search_successful_queue_poll_updates_last_success_timestamp(
    monkeypatch, metadata
) -> None:
    timestamp = _Timestamp()
    monkeypatch.setattr(module, "SEARCH_LAST_SUCCESSFUL_POLL", timestamp)

    async def stop_after_poll(seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", stop_after_poll)
    with pytest.raises(asyncio.CancelledError):
        await module.SearchService()._poll()

    assert len(timestamp.values) == 1
    assert timestamp.values[0] > 0


@pytest.mark.asyncio
async def test_search_reconciliation_failure_counts_one_index(monkeypatch, metadata) -> None:
    errors = _Counter()
    monkeypatch.setattr(module, "SEARCH_RECONCILIATION_ERRORS", errors)
    row = dict.fromkeys(module._INDEX_FIELDS)
    row.update(
        name="private_index",
        active_version=1,
        key_columns=[],
        content_columns=[],
        filter_columns=[],
    )
    metadata.list_indexes.return_value = [row]
    metadata.get_sync_state.side_effect = RuntimeError("offline")
    service = module.SearchService()

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

    async def build_claimed(definition, version, generation, previous):
        assert active.value == 1 and generation == 7
        return built

    monkeypatch.setattr(
        service,
        "_claim",
        AsyncMock(
            return_value=(
                {"name": "private_index"},
                {"version": 1, "build_status": "PENDING"},
                None,
                7,
            )
        ),
    )
    monkeypatch.setattr(service, "_build_claimed", build_claimed)

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

    service = module.SearchService()
    finish = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_finish", finish)
    completed = await service._build_claimed(definition, version, 7, None)
    assert completed is False
    finish.assert_awaited_once_with("private_index", 1, 7, failure_code="RuntimeError")
