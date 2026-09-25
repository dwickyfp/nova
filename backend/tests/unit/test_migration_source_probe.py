"""An unsaved migration source can be tested without exposing its credential."""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.migration import router, service
from app.modules.migration.job_worker import MigrationJobWorker
from app.modules.migration.schemas import SourceConnectionTestResponse
from app.modules.migration.source import SourceConnectionError


def test_source_probe_route_dispatches_to_worker(monkeypatch):
    calls = []

    async def worker_read(operation, body, user):
        calls.append((operation, body.model_dump(), user["username"]))
        return {"connected": True}

    monkeypatch.setattr(router, "_worker_read", worker_read)
    app = FastAPI()
    app.include_router(router.router, prefix="/api/v1/migration")
    app.dependency_overrides[router.get_current_user] = lambda: {"username": "alice"}

    response = TestClient(app).post(
        "/api/v1/migration/sources/test",
        json={
            "source": "draft",
            "host": "10.0.0.12",
            "port": 9030,
            "username": "reader",
            "secret_ref": "env://SOURCE_PASSWORD",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"connected": True}
    assert calls == [
        (
            "test_source",
            {
                "source": "draft",
                "host": "10.0.0.12",
                "port": 9030,
                "username": "reader",
                "secret_ref": "env://SOURCE_PASSWORD",
            },
            "alice",
        )
    ]


@pytest.mark.asyncio
async def test_worker_probe_uses_unsaved_address(monkeypatch):
    captured = []

    async def probe(**kwargs):
        captured.append(kwargs)
        return SourceConnectionTestResponse(connected=True)

    monkeypatch.setattr(service.migration_service, "test_source_connection", probe)
    result = await MigrationJobWorker(None)._read(
        {"operation": "test_source", "actor": "alice", "active_role": None},
        {
            "source": "draft",
            "host": "10.0.0.12",
            "port": 9030,
            "username": "reader",
            "secret_ref": "env://SOURCE_PASSWORD",
        },
        "session-1",
        {"encrypted_password": "not-used-for-source"},
    )

    assert result == {"connected": True}
    assert captured == [
        {
            "name": "draft",
            "host": "10.0.0.12",
            "port": 9030,
            "username": "reader",
            "secret_ref": "env://SOURCE_PASSWORD",
        }
    ]


@pytest.mark.asyncio
async def test_probe_runs_select_one_and_redacts_query_failure(monkeypatch):
    statements = []
    fail_query = False

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            statements.append(statement)
            if fail_query:
                raise RuntimeError("SECRET_SENTINEL in driver error")

        async def fetchone(self):
            return (1,)

    class Connection:
        def cursor(self):
            return Cursor()

    @asynccontextmanager
    async def open_draft_source(source):
        assert source.host == "10.0.0.12"
        assert source.secret_ref == "env://SOURCE_PASSWORD"
        yield Connection()

    monkeypatch.setattr(service, "open_source_connection", open_draft_source)
    args = {
        "name": "draft",
        "host": "10.0.0.12",
        "port": 9030,
        "username": "reader",
        "secret_ref": "env://SOURCE_PASSWORD",
    }

    assert (await service.migration_service.test_source_connection(**args)).connected
    assert statements == ["SELECT 1"]

    fail_query = True
    with pytest.raises(SourceConnectionError) as error:
        await service.migration_service.test_source_connection(**args)
    assert "SECRET_SENTINEL" not in str(error.value)
