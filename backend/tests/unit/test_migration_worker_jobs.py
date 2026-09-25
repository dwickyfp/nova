"""The API queues migration work; only the worker invokes migration operations."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.migration import job_worker, jobs, router
from app.modules.migration.schemas import (
    DatabasesRequest,
    DatabasesResponse,
    EnumerateRequest,
    ExecuteResponse,
    ExecuteStepResult,
    PlanStepKind,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def aclose(self) -> None:
        pass


class FakeJobRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create(self, **kwargs: Any) -> None:
        self.rows[kwargs["job_id"]] = {
            "id": kwargs["job_id"],
            "operation": kwargs["operation"],
            "actor": kwargs["actor"],
            "session_fingerprint": kwargs["session_fingerprint"],
            "active_role": kwargs["role"],
            "security_context_version": kwargs["security_context_version"],
            "source_name": kwargs["source"],
            "request_json": json.dumps(kwargs["request"]),
            "status": "queued",
            "current_database": None,
            "result_json": None,
            "error_code": None,
            "created_at": datetime(2026, 9, 24),
            "started_at": None,
            "heartbeat_at": None,
            "finished_at": None,
        }

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)

    async def queued(self, limit: int = 4) -> list[dict[str, Any]]:
        return sorted(
            (row for row in self.rows.values() if row["status"] == "queued"),
            key=lambda row: row["operation"] in {"execute", "execute_batch"},
        )[:limit]

    async def claim(self, job_id: str) -> bool:
        row = self.rows[job_id]
        if row["status"] != "queued":
            return False
        row["status"] = "running"
        return True

    async def progress(self, job_id: str, *, current_database: str | None, result: dict) -> None:
        row = self.rows[job_id]
        row["current_database"] = current_database
        row["result_json"] = json.dumps(jobs.SanitizingJSONResponse._sanitize(result))

    async def finish(
        self,
        job_id: str,
        *,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
    ) -> None:
        row = self.rows[job_id]
        row["status"] = status
        row["result_json"] = (
            json.dumps(jobs.SanitizingJSONResponse._sanitize(result))
            if result is not None
            else None
        )
        row["error_code"] = error_code

    async def heartbeat(self, job_id: str) -> None:
        pass

    async def interrupt_stale(self) -> None:
        pass

    async def prune_completed_reads(self) -> None:
        pass

    async def delete(self, job_id: str) -> None:
        del self.rows[job_id]


class FakeSessions:
    def __init__(self) -> None:
        self.active = True
        self.reads = 0
        self.refreshes = 0

    async def get(self, session_id: str) -> dict | None:
        self.reads += 1
        if not self.active or session_id != "session-1":
            return None
        return {
            "username": "alice",
            "encrypted_password": "ENCRYPTED_ONLY_IN_REDIS_SESSION",
            "active_role": "ACCOUNTADMIN",
            "security_context_version": 1,
        }

    async def refresh(self, session_id: str) -> None:
        if self.active and session_id == "session-1":
            self.refreshes += 1


@pytest.fixture
def environment(monkeypatch):
    store = FakeJobRepo()
    redis = FakeRedis()
    sessions = FakeSessions()
    monkeypatch.setattr(jobs, "migration_job_repo", store)
    monkeypatch.setattr(job_worker, "migration_job_repo", store)
    monkeypatch.setattr(router, "migration_job_repo", store)
    monkeypatch.setattr(jobs.aioredis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(job_worker, "session_store", sessions)
    monkeypatch.setattr(jobs.settings, "SECRET_KEY", "migration-test-key")
    monkeypatch.setattr(router.settings, "MIGRATION_EXECUTE_ENABLED", True)

    async def audit(**kwargs):
        return "audit-id"

    monkeypatch.setattr(jobs, "write_audit_log", audit)
    monkeypatch.setattr(job_worker, "write_audit_log", audit)

    async def databases(source: str) -> DatabasesResponse:
        return DatabasesResponse(source=source, databases=["db_one", "db_two"], count=2)

    monkeypatch.setattr(job_worker.migration_service, "databases", databases)
    app = FastAPI()
    app.include_router(router.router, prefix="/api/v1/migration")
    app.dependency_overrides[router.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "session-1",
        "active_role": "ACCOUNTADMIN",
        "security_context_version": 1,
        "encrypted_password": "ENCRYPTED_ONLY_IN_REDIS_SESSION",
    }
    return TestClient(app, raise_server_exceptions=False), store, redis, sessions, app


def _batch_payload() -> dict[str, Any]:
    return {
        "source": "source_one",
        "databases": ["db_one", "db_two"],
        "acknowledge_omissions": True,
        "confirmation": "MIGRATE 2 DATABASES",
    }


def test_api_only_queues_and_worker_reports_partial_per_database(environment, monkeypatch):
    client, store, redis, _, app = environment
    calls: list[str] = []

    async def execute(source: str, database: str, **kwargs):
        calls.append(database)
        failed = int(database == "db_two")
        return ExecuteResponse(
            source_database=database,
            target_database=database,
            results=[
                ExecuteStepResult(
                    order=1,
                    kind=PlanStepKind.TABLE,
                    object_name="sample",
                    statement="CREATE TABLE sample (id INT)",
                    status="failed" if failed else "ok",
                    error="aws.s3.secret_key='SENTINEL'" if failed else None,
                )
            ],
            succeeded=1 - failed,
            failed=failed,
            skipped=0,
        )

    monkeypatch.setattr(job_worker.migration_service, "execute", execute)
    queued = client.post("/api/v1/migration/execute-batch", json=_batch_payload())
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    assert calls == []
    assert store.rows[job_id]["status"] == "queued"
    assert "ENCRYPTED_ONLY_IN_REDIS_SESSION" not in store.rows[job_id]["request_json"]
    assert redis.values[f"nova:migration:job-session:{job_id}"] == "session-1"

    async def process():
        worker = job_worker.MigrationJobWorker(redis)
        assert await worker.run_once() == 1
        await asyncio.gather(*worker._active.values())

    asyncio.run(process())
    assert calls == ["db_one", "db_two"]
    status = client.get(f"/api/v1/migration/jobs/{job_id}")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "partial"
    assert [item["status"] for item in body["results"]] == ["succeeded", "failed"]
    assert body["results"][1]["steps"][0]["object_name"] == "sample"
    assert "SENTINEL" not in status.text
    assert "SENTINEL" not in store.rows[job_id]["result_json"]

    app.dependency_overrides[router.get_current_user] = lambda: {
        "username": "bob",
        "session_id": "session-2",
        "active_role": "ACCOUNTADMIN",
    }
    assert client.get(f"/api/v1/migration/jobs/{job_id}").status_code == 404
    app.dependency_overrides[router.get_current_user] = lambda: {
        "username": "alice",
        "session_id": "new-session-after-login",
        "active_role": "ACCOUNTADMIN",
        "assigned_roles": ["ACCOUNTADMIN"],
        "security_context_version": 1,
    }
    assert client.get(f"/api/v1/migration/jobs/{job_id}").status_code == 200


def test_expired_session_fails_closed_before_execute(environment, monkeypatch):
    client, store, redis, sessions, _ = environment
    called = False

    async def execute(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("execute should not be called")

    monkeypatch.setattr(job_worker.migration_service, "execute", execute)
    job_id = client.post("/api/v1/migration/execute-batch", json=_batch_payload()).json()["job_id"]
    sessions.active = False

    async def process():
        worker = job_worker.MigrationJobWorker(redis)
        await worker.run_once()
        await asyncio.gather(*worker._active.values())

    asyncio.run(process())
    assert called is False
    assert store.rows[job_id]["status"] == "failed"
    assert store.rows[job_id]["error_code"] == "session_expired"


def test_read_job_completes_while_execute_waits(environment, monkeypatch):
    _, store, redis, _, _ = environment
    release = asyncio.Event()

    async def execute(source: str, database: str, **kwargs):
        await release.wait()
        return ExecuteResponse(
            source_database=database,
            target_database=database,
            results=[],
            succeeded=0,
            failed=0,
            skipped=0,
        )

    monkeypatch.setattr(job_worker.migration_service, "execute", execute)
    user = {
        "username": "alice",
        "session_id": "session-1",
        "active_role": "ACCOUNTADMIN",
        "security_context_version": 1,
    }

    async def scenario():
        batch = await jobs.submit_job(
            "execute_batch",
            job_worker.ExecuteBatchRequest.model_validate(_batch_payload()),
            user,
        )
        discovery = await jobs.submit_job("databases", DatabasesRequest(source="source_one"), user)
        worker = job_worker.MigrationJobWorker(redis, max_concurrent=2)
        assert await worker.run_once() == 2
        for _ in range(20):
            if store.rows[discovery.job_id]["status"] == "succeeded":
                break
            await asyncio.sleep(0)
        assert store.rows[discovery.job_id]["status"] == "succeeded"
        assert store.rows[batch.job_id]["status"] == "running"
        release.set()
        await asyncio.gather(*worker._active.values())

    asyncio.run(scenario())


def test_heartbeat_keeps_live_session_fresh(environment):
    _, _, redis, sessions, _ = environment

    async def scenario():
        worker = job_worker.MigrationJobWorker(redis, heartbeat_interval=0.001)
        stop = asyncio.Event()
        task = asyncio.create_task(worker._heartbeat("job-id", "session-1", stop))
        await asyncio.sleep(0.01)
        stop.set()
        await task

    asyncio.run(scenario())
    assert sessions.refreshes > 0


def test_logout_between_databases_stops_remaining_work(environment, monkeypatch):
    client, store, redis, sessions, _ = environment
    calls: list[str] = []

    async def execute(source: str, database: str, **kwargs):
        calls.append(database)
        sessions.active = False
        return ExecuteResponse(
            source_database=database,
            target_database=database,
            results=[],
            succeeded=0,
            failed=0,
            skipped=0,
        )

    monkeypatch.setattr(job_worker.migration_service, "execute", execute)
    job_id = client.post("/api/v1/migration/execute-batch", json=_batch_payload()).json()["job_id"]

    async def process():
        worker = job_worker.MigrationJobWorker(redis)
        await worker.run_once()
        await asyncio.gather(*worker._active.values())

    asyncio.run(process())
    assert calls == ["db_one"]
    results = json.loads(store.rows[job_id]["result_json"])["results"]
    assert [item["status"] for item in results] == ["succeeded", "failed"]
    assert results[1]["error"] == "session_expired"


def test_worker_logs_exception_class_without_error_message(environment, monkeypatch, caplog):
    _, store, redis, _, _ = environment

    async def fail_enumerate(*args):
        raise RuntimeError("SECRET_SENTINEL in statement")

    monkeypatch.setattr(job_worker.migration_service, "enumerate", fail_enumerate)
    user = {
        "username": "alice",
        "session_id": "session-1",
        "active_role": "ACCOUNTADMIN",
        "security_context_version": 1,
    }

    async def process():
        accepted = await jobs.submit_job(
            "enumerate", EnumerateRequest(source="source_one", database="db_one"), user
        )
        worker = job_worker.MigrationJobWorker(redis)
        await worker.run_once()
        await asyncio.gather(*worker._active.values())
        return accepted.job_id

    job_id = asyncio.run(process())
    assert store.rows[job_id]["error_code"] == "migration_failed"
    assert "operation=enumerate" in caplog.text
    assert "exception=builtins.RuntimeError" in caplog.text
    assert "errno=None" in caplog.text
    assert "SECRET_SENTINEL" not in caplog.text


def test_numeric_errno_only_logs_integer_code():
    assert job_worker._numeric_errno(RuntimeError(1064, "SECRET_SENTINEL")) == 1064
    assert job_worker._numeric_errno(RuntimeError("SECRET_SENTINEL")) is None


def test_job_status_reads_return_safe_503_when_system_database_fails(
    environment, monkeypatch, caplog
):
    client, store, _, _, _ = environment

    async def fail_get(job_id: str):
        raise RuntimeError("SECRET_SENTINEL in database failure")

    monkeypatch.setattr(store, "get", fail_get)
    read = client.post(
        "/api/v1/migration/enumerate",
        json={"source": "source_one", "database": "db_one"},
    )
    status = client.get("/api/v1/migration/jobs/job-id")

    assert read.status_code == status.status_code == 503
    assert read.json()["detail"] == "Migration job status is temporarily unavailable"
    assert status.json()["detail"] == "Migration job status is temporarily unavailable"
    assert "SECRET_SENTINEL" not in read.text + status.text + caplog.text


def test_successful_read_result_survives_cleanup_failure(environment, monkeypatch, caplog):
    _, store, _, _, _ = environment
    store.rows["read-job"] = {
        "id": "read-job",
        "status": "succeeded",
        "result_json": json.dumps({"database": "db_one", "count": 2}),
    }

    async def fail_delete(job_id: str):
        raise RuntimeError("SECRET_SENTINEL in cleanup failure")

    monkeypatch.setattr(store, "delete", fail_delete)
    result = asyncio.run(jobs.wait_for_job("read-job"))

    assert result == {"database": "db_one", "count": 2}
    assert store.rows["read-job"]["status"] == "succeeded"
    assert "migration read job cleanup failed" in caplog.text
    assert "SECRET_SENTINEL" not in caplog.text
