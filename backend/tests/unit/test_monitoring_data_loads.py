"""Data loads: one cached scan of the engine's load history, not two per request."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.modules.monitoring import repository as repo_module
from app.modules.monitoring.repository import MonitoringRepository

pytestmark = pytest.mark.asyncio


def _row(label: str, db: str = "SALES", state: str = "FINISHED", kind: str = "INSERT") -> tuple:
    return (label, db, "orders", kind, state, "100%", "2026-11-09 10:00:00", None, None,
            None, None, 10, 10)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[Any]]]:
    calls: list[tuple[str, list[Any]]] = []

    async def execute_system(sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        calls.append((sql, list(params or [])))
        if "COUNT(*)" in sql:
            return {"rows": [(1,)]}
        return {"rows": [_row("a"), _row("b", state="CANCELLED"), _row("c", kind="BROKER")]}

    monkeypatch.setattr(repo_module.db, "execute_system", execute_system)
    return calls


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    from app.core.redis import session_store

    fake = FakeRedis()
    monkeypatch.setattr(session_store, "_redis", fake)
    return fake


async def test_unfiltered_history_is_one_scan_that_skips_bookkeeping(engine, redis):
    repo = MonitoringRepository()
    first = await repo.get_data_loads(limit=2, offset=0)
    second = await repo.get_data_loads(limit=2, offset=2)

    assert len(engine) == 1  # one scan serves the count and every page
    sql, params = engine[0]
    assert "NOT IN ('_statistics_', 'NOVA_SYSTEM')" in sql and "COUNT(*)" not in sql
    assert params == [repo_module._LOADS_SCAN_LIMIT]
    assert first["total"] == second["total"] == 3
    assert [item["label"] for item in first["items"]] == ["a", "b"]
    assert [item["label"] for item in second["items"]] == ["c"]


async def test_state_and_type_filter_the_cached_scan(engine, redis):
    repo = MonitoringRepository()
    cancelled = await repo.get_data_loads(state="cancelled")
    broker = await repo.get_data_loads(load_type="BROKER")
    assert [item["label"] for item in cancelled["items"]] == ["b"] and cancelled["total"] == 1
    assert [item["label"] for item in broker["items"]] == ["c"]
    assert len(engine) == 1


async def test_a_database_filter_goes_straight_to_the_engine(engine, redis):
    result = await MonitoringRepository().get_data_loads(db_name="NOVA_SYSTEM", state="finished")
    assert all("DB_NAME = %s" in sql for sql, _ in engine)
    assert engine[0][1] == ["NOVA_SYSTEM", "FINISHED"]
    assert result["total"] == 1
    assert "nova:monitoring:loads:recent" not in redis.data


async def test_a_stale_answer_is_served_while_one_scan_refreshes_it(engine, redis, monkeypatch):
    redis.data[repo_module._LOADS_CACHE_KEY] = json.dumps(
        {"at": 0, "items": [{"label": "old", "state": "FINISHED", "load_type": "INSERT"}]}
    )
    repo = MonitoringRepository()
    result = await repo.get_data_loads()
    assert [item["label"] for item in result["items"]] == ["old"]

    await asyncio.gather(*list(repo_module._BACKGROUND))
    assert len(engine) == 1
    refreshed = json.loads(redis.data[repo_module._LOADS_CACHE_KEY])
    assert [item["label"] for item in refreshed["items"]] == ["a", "b", "c"]
    assert repo_module._LOADS_REFRESH_LOCK not in redis.data

    # While a refresh is already running, another visit does not start a second.
    redis.data[repo_module._LOADS_CACHE_KEY] = json.dumps({"at": 0, "items": []})
    redis.data[repo_module._LOADS_REFRESH_LOCK] = "1"
    await repo.get_data_loads()
    assert not repo_module._BACKGROUND and len(engine) == 1


async def test_without_redis_every_call_asks_the_engine(engine, monkeypatch):
    from app.core.redis import session_store

    monkeypatch.setattr(session_store, "_redis", None)
    repo = MonitoringRepository()
    await repo.get_data_loads()
    await repo.get_data_loads()
    assert len(engine) == 2


async def test_load_stats_scan_once_then_serve_from_cache(monkeypatch, redis):
    calls: list[str] = []

    async def execute_system(sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        calls.append(sql)
        return {"rows": [(10, 7, 2, 1, 0)]}

    monkeypatch.setattr(repo_module.db, "execute_system", execute_system)
    repo = MonitoringRepository()
    first = await repo.get_load_stats()
    second = await repo.get_load_stats()
    assert first == second == {"total": 10, "finished": 7, "cancelled": 2, "loading": 1, "other": 0}
    assert len(calls) == 1 and "information_schema.loads" in calls[0]
    assert repo_module._STATS_CACHE_KEY in redis.data


async def test_stale_load_stats_are_returned_while_one_scan_refreshes(monkeypatch, redis):
    async def execute_system(sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        return {"rows": [(3, 3, 0, 0, 0)]}

    monkeypatch.setattr(repo_module.db, "execute_system", execute_system)
    old = {"total": 1, "finished": 1, "cancelled": 0, "loading": 0, "other": 0}
    redis.data[repo_module._STATS_CACHE_KEY] = json.dumps({"at": 0, "items": old})
    repo = MonitoringRepository()
    assert await repo.get_load_stats() == old
    await asyncio.gather(*list(repo_module._BACKGROUND))
    assert json.loads(redis.data[repo_module._STATS_CACHE_KEY])["items"]["total"] == 3
    assert repo_module._STATS_REFRESH_LOCK not in redis.data


async def test_alert_probes_run_concurrently_and_keep_their_failures_apart(monkeypatch):
    started = asyncio.Barrier(2)

    async def execute_system(sql: str, params: list[Any] | None = None) -> dict[str, Any]:
        if sql in {"SHOW FRONTENDS", "SHOW BACKENDS"}:
            await asyncio.wait_for(started.wait(), 1)  # both must be in flight at once
            return {"columns": ["Alive"], "rows": [("true",)]}
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(repo_module.db, "execute_system", execute_system)
    repo = MonitoringRepository()
    monkeypatch.setattr(repo, "_rows_to_maps", lambda result: [{"Alive": "true"}])
    metrics = await repo.collect_alert_metrics()
    assert metrics.engine_reachable is True
    assert metrics.fe_total == metrics.be_total == 1
    assert metrics.max_compaction_score is None and metrics.be_compaction_failures is None
