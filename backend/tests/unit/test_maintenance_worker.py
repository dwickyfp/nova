"""Maintenance runs outside request lifetimes and fails visibly if a loop stops."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.modules.catalog import refresher as catalog
from app.modules.intelligence import newsroom_refresher as news
from app.modules.intelligence import search
from app.modules.ml_engine.service import ml_engine_service
from app.worker.maintenance import MaintenanceWorker


@pytest.mark.parametrize("failure", [None, "catalog", "news", "search", "ml-retention"])
async def test_all_loops_are_supervised_and_cleaned_up(monkeypatch, failure):
    stop = asyncio.Event()
    started, ended = set(), set()

    async def run(name):
        started.add(name)
        try:
            await asyncio.sleep(0)
            if name == failure:
                raise RuntimeError("fixture failure")
            if failure is None and len(started) == 4:
                stop.set()
            await asyncio.Event().wait()
        finally:
            ended.add(name)

    schema = AsyncMock()
    monkeypatch.setattr(ml_engine_service.ephemeral_repository, "ensure_schema", schema)
    monkeypatch.setattr(catalog, "run", lambda stop: run("catalog"))
    monkeypatch.setattr(news, "run", lambda stop: run("news"))
    monkeypatch.setattr(search.search_service, "run", lambda: run("search"))
    monkeypatch.setattr(ml_engine_service, "sweep_ephemeral", lambda: run("ml-retention"))
    if failure:
        with pytest.raises(ExceptionGroup) as exc:
            await asyncio.wait_for(MaintenanceWorker().run_forever(stop), 1)
        assert any(isinstance(e, RuntimeError) for e in exc.value.exceptions)
    else:
        await asyncio.wait_for(MaintenanceWorker().run_forever(stop), 1)
    assert started == ended == {"catalog", "news", "search", "ml-retention"}
    schema.assert_awaited_once()


async def test_search_cancellation_drains_the_builds(monkeypatch):
    service = search.SearchService()
    started, ended = asyncio.Event(), asyncio.Event()

    async def build():
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    async def poll():
        service._tasks.add(asyncio.create_task(build()))
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_poll", poll)
    task = asyncio.create_task(service.run())
    await started.wait()
    with pytest.raises(RuntimeError, match="already running"):
        await service.run()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ended.is_set()
    assert service._poller is None
    assert all(task.done() for task in service._tasks)


async def test_search_poll_failure_cleans_up_before_propagating(monkeypatch):
    service = search.SearchService()
    ended = asyncio.Event()

    async def build():
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()

    async def poll():
        service._tasks.add(asyncio.create_task(build()))
        await asyncio.sleep(0)
        raise ValueError("fixture poll failure")

    monkeypatch.setattr(service, "_poll", poll)
    with pytest.raises(ValueError, match="fixture poll failure"):
        await service.run()
    assert ended.is_set()
    assert not service._tasks and service._poller is None


@asynccontextmanager
async def unlocked(*args, **kwargs):
    yield


@pytest.mark.parametrize("operation", ["create", "rebuild", "retry"])
async def test_requests_persist_work_without_starting_a_build(monkeypatch, operation):
    service = search.SearchService()
    user = {"username": "fixture", "active_role": "reader", "session_id": "fixture-login"}
    definition = {"id": "index-id", "model_alias": None, "owner_name": "fixture"}
    monkeypatch.setattr(service, "_lock", unlocked)
    monkeypatch.setattr(service, "_source_access", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value=definition))
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_version", AsyncMock(return_value={"build_status": "FAILED"}))
    monkeypatch.setattr(service, "describe", AsyncMock(return_value={"status": "BUILDING"}))
    monkeypatch.setattr(
        search.vector_backend, "health", AsyncMock(return_value={"full_text": True})
    )
    writes = AsyncMock(return_value={"rows": [[1]]})
    monkeypatch.setattr(search.db, "execute_system", writes)
    monkeypatch.setattr(search, "write_audit_log", AsyncMock())
    forbidden = Mock(side_effect=AssertionError("HTTP request started a Search build"))
    monkeypatch.setattr(service, "_schedule", forbidden)
    if operation == "create":
        await service.create(
            search.SearchIndexCreate(
                name="fixture_index",
                source_relation="fixture.data",
                key_columns=["id"],
                content_columns=["content"],
            ),
            user,
        )
    elif operation == "rebuild":
        await service.rebuild("fixture_index", search.SearchRebuild(), user)
    else:
        await service.retry("fixture_index", 1, user)
    assert any("PENDING" in call.args[0] for call in writes.call_args_list)
    forbidden.assert_not_called()
    assert not service._tasks


@pytest.mark.parametrize("operation", ["create", "rebuild", "retry"])
async def test_unauthorized_requests_do_not_queue_work(monkeypatch, operation):
    service = search.SearchService()
    monkeypatch.setattr(service, "_source_access", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_owned", AsyncMock(return_value={"owner_name": "fixture"}))
    write = AsyncMock()
    monkeypatch.setattr(search.db, "execute_system", write)
    with pytest.raises(HTTPException) as exc:
        if operation == "create":
            await service.create(
                search.SearchIndexCreate(
                    name="fixture_index",
                    source_relation="fixture.data",
                    key_columns=["id"],
                    content_columns=["content"],
                ),
                {"username": "fixture"},
            )
        elif operation == "rebuild":
            await service.rebuild("fixture_index", search.SearchRebuild(), {"username": "fixture"})
        else:
            await service.retry("fixture_index", 1, {"username": "fixture"})
    assert exc.value.status_code == 403
    write.assert_not_awaited()
