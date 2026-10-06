"""Training handed to ``nova-worker`` stays bound to the caller's session.

The worker must train as the user who asked, with that user's one active role
and security-context version, and must refuse when the session it was queued
from is gone or has changed. It never falls back to another identity.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import encrypt_password
from app.modules.ml_engine import job_worker, jobs
from app.modules.ml_engine.job_worker import MLJobWorker
from app.modules.ml_engine.jobs import MLJobSessionRequired, MLJobUnavailable

USER = {
    "username": "alice",
    "active_role": "analyst",
    "security_context_version": 4,
    "tenant": "default",
}
REQUEST = {
    "model_name": "churn",
    "model_type": "classification",
    "algorithm": "auto",
    "target_column": "churned",
    "feature_columns": ["tenure"],
    "hyperparameters": None,
    "test_size": 0.2,
    "database_name": "analytics",
    "timestamp_column": None,
    "series_column": None,
    "horizon": None,
    "frequency": None,
    "mode": "balanced",
}
SQL = "SELECT tenure, churned FROM customers WHERE token = 'literal-in-sql'"
RESULT = {"model_id": "m-1", "model_name": "churn", "status": "READY", "training_rows": 10}


class Repo:
    """In-memory stand-in with the conditional updates the real table performs."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def create(
        self,
        *,
        job_id,
        actor,
        fingerprint,
        role,
        security_context_version,
        tenant,
        request,
        encrypted_sql,
    ) -> None:
        self.rows[job_id] = {
            "id": job_id,
            "actor": actor,
            "session_fingerprint": fingerprint,
            "active_role": role,
            "security_context_version": security_context_version,
            "tenant": tenant,
            "request_json": json.dumps(request),
            "encrypted_sql": encrypted_sql,
            "status": "queued",
            "cancel_requested": False,
            "result_json": None,
            "error_kind": None,
            "error_message": None,
            "created_at": None,
            "started_at": None,
            "finished_at": None,
        }

    async def get(self, job_id):
        row = self.rows.get(job_id)
        return dict(row) if row else None

    async def queued(self, limit):
        return [dict(r) for r in self.rows.values() if r["status"] == "queued"][:limit]

    async def claim(self, job_id) -> bool:
        row = self.rows[job_id]
        if row["status"] != "queued" or row["cancel_requested"]:
            return False
        row["status"] = "running"
        return True

    async def heartbeat(self, job_id) -> bool:
        return self.rows[job_id]["cancel_requested"]

    async def request_cancel(self, job_id) -> None:
        if self.rows[job_id]["status"] in {"queued", "running"}:
            self.rows[job_id]["cancel_requested"] = True

    async def finish(
        self,
        job_id,
        *,
        status,
        expected="running",
        result=None,
        error_kind=None,
        error_message=None,
    ) -> bool:
        row = self.rows[job_id]
        if row["status"] != expected:
            return False
        row.update(
            status=status,
            result_json=json.dumps(result) if result is not None else None,
            error_kind=error_kind,
            error_message=error_message,
        )
        return True

    async def interrupt_stale(self, **kwargs) -> None:
        pass

    async def prune_finished(self) -> None:
        pass


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def aclose(self):
        pass


@pytest.fixture
def queue(monkeypatch):
    repo, redis = Repo(), Redis()
    session = {
        "username": "alice",
        "active_role": "analyst",
        "security_context_version": 4,
        "encrypted_password": encrypt_password("alice-password"),
    }
    sessions = {"session-1": session}
    monkeypatch.setattr(jobs, "ml_job_repo", repo)
    monkeypatch.setattr(job_worker, "ml_job_repo", repo)
    monkeypatch.setattr(jobs.aioredis, "from_url", lambda *args, **kwargs: redis)
    monkeypatch.setattr(jobs, "write_audit_log", AsyncMock())
    monkeypatch.setattr(job_worker, "write_audit_log", AsyncMock())
    monkeypatch.setattr(job_worker.session_store, "get", AsyncMock(side_effect=sessions.get))
    monkeypatch.setattr(job_worker.session_store, "refresh", AsyncMock())
    monkeypatch.setattr(settings, "ML_JOB_POLL_SECONDS", 0.001)
    return repo, redis, session, sessions


@pytest.fixture
def trainer(monkeypatch):
    from app.modules.ml_engine.service import ml_engine_service

    train = AsyncMock(return_value=RESULT)
    monkeypatch.setattr(ml_engine_service, "train_model_locally", train)
    return train


async def _submit() -> str:
    return await jobs.submit_training(
        session_id="session-1", user=USER, training_sql=SQL, request=dict(REQUEST)
    )


# ── Queueing ────────────────────────────────────────────────────────────────


async def test_a_job_names_the_session_and_stores_no_credential(queue):
    repo, redis, session, _ = queue

    job_id = await _submit()

    row = repo.rows[job_id]
    stored = json.dumps(row)
    assert (row["actor"], row["active_role"], row["security_context_version"]) == (
        "alice",
        "analyst",
        4,
    )
    assert redis.values == {f"nova:ml:job-session:{job_id}": "session-1"}
    assert "session-1" not in stored and session["encrypted_password"] not in stored
    assert "alice-password" not in stored
    # The SQL is readable only by a process holding the encryption key.
    assert "literal-in-sql" not in stored and jobs.training_sql(row) == SQL


async def test_queueing_requires_a_session(queue):
    with pytest.raises(MLJobSessionRequired):
        await jobs.submit_training(session_id=None, user=USER, training_sql=SQL, request=REQUEST)


async def test_queueing_rejects_options_the_worker_would_not_understand(queue):
    with pytest.raises(ValueError, match="password"):
        await jobs.submit_training(
            session_id="session-1",
            user=USER,
            training_sql=SQL,
            request={**REQUEST, "password": "smuggled"},
        )


async def test_session_link_is_removed_when_the_job_cannot_be_stored(queue):
    repo, redis, _, _ = queue
    repo.create = AsyncMock(side_effect=ConnectionError("engine down"))

    with pytest.raises(ConnectionError):
        await _submit()

    assert redis.values == {}


# ── Worker ──────────────────────────────────────────────────────────────────


async def _run_one(redis) -> None:
    worker = MLJobWorker(redis, max_concurrent=2, heartbeat_interval=0.005)
    assert await worker.run_once() == 1
    await asyncio.gather(*worker._active.values())


async def test_worker_trains_as_the_caller_with_the_callers_role_and_context(queue, trainer):
    repo, redis, _, _ = queue
    job_id = await _submit()

    await _run_one(redis)

    call = trainer.await_args.kwargs
    assert (call["username"], call["password"], call["role"]) == (
        "alice",
        "alice-password",
        "analyst",
    )
    assert call["security_context_version"] == 4 and call["training_sql"] == SQL
    assert call["model_name"] == "churn" and call["mode"] == "balanced"
    assert "as_system" not in call
    assert repo.rows[job_id]["status"] == "succeeded"
    assert json.loads(repo.rows[job_id]["result_json"]) == RESULT
    assert redis.values == {}


@pytest.mark.parametrize(
    "change",
    [
        {"username": "mallory"},
        {"active_role": "admin"},
        {"security_context_version": 5},
        {"encrypted_password": ""},
        None,
    ],
)
async def test_worker_refuses_when_the_session_is_gone_or_changed(queue, trainer, change):
    repo, redis, session, sessions = queue
    job_id = await _submit()
    if change is None:
        sessions.clear()
    else:
        session.update(change)

    await _run_one(redis)

    trainer.assert_not_awaited()
    row = repo.rows[job_id]
    assert (row["status"], row["error_kind"]) == ("failed", "session_expired")


async def test_worker_refuses_a_job_whose_session_link_is_missing(queue, trainer):
    repo, redis, _, _ = queue
    job_id = await _submit()
    redis.values.clear()

    await _run_one(redis)

    trainer.assert_not_awaited()
    assert repo.rows[job_id]["error_kind"] == "session_expired"


async def test_invalid_training_request_is_reported_without_secrets(queue, trainer):
    repo, redis, _, _ = queue
    trainer.side_effect = ValueError("Unknown column; 'aws.s3.secret_key'='hunter2-secret'")
    job_id = await _submit()

    await _run_one(redis)

    row = repo.rows[job_id]
    assert (row["status"], row["error_kind"]) == ("failed", "invalid_request")
    assert "hunter2-secret" not in row["error_message"]


async def test_unexpected_failure_is_recorded(queue, trainer):
    repo, redis, _, _ = queue
    trainer.side_effect = RuntimeError("worker process died")
    job_id = await _submit()

    await _run_one(redis)

    assert (repo.rows[job_id]["status"], repo.rows[job_id]["error_kind"]) == ("failed", "failed")


async def test_caller_cancellation_stops_the_training(queue, trainer):
    repo, redis, _, _ = queue
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def train(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    trainer.side_effect = train
    job_id = await _submit()
    worker = MLJobWorker(redis, heartbeat_interval=0.005)
    await worker.run_once()
    await started.wait()

    await repo.request_cancel(job_id)
    await asyncio.gather(*worker._active.values())

    assert stopped.is_set() and repo.rows[job_id]["status"] == "cancelled"


async def test_stopping_the_worker_stops_its_training_and_marks_the_job(queue, trainer):
    repo, redis, _, _ = queue
    started, stopped = asyncio.Event(), asyncio.Event()

    async def train(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    trainer.side_effect = train
    job_id = await _submit()
    worker = MLJobWorker(redis, heartbeat_interval=1)
    await worker.run_once()
    await started.wait()

    for task in worker._active.values():
        task.cancel()
    await asyncio.gather(*worker._active.values(), return_exceptions=True)

    assert stopped.is_set() and repo.rows[job_id]["status"] == "interrupted"


async def test_worker_keeps_the_session_alive_while_it_trains(queue, trainer):
    _, redis, _, _ = queue

    async def train(**kwargs):
        await asyncio.sleep(0.03)
        return RESULT

    trainer.side_effect = train
    await _submit()

    await _run_one(redis)

    job_worker.session_store.refresh.assert_awaited_with("session-1")


async def test_a_job_is_claimed_by_one_worker_only(queue, trainer):
    _, redis, _, _ = queue
    await _submit()
    first, second = MLJobWorker(redis), MLJobWorker(redis)

    claimed = await asyncio.gather(first.run_once(), second.run_once())
    await asyncio.gather(*first._active.values(), *second._active.values())

    assert sorted(claimed) == [0, 1] and trainer.await_count == 1


async def test_worker_respects_its_concurrency(queue, trainer):
    _, redis, _, _ = queue
    release = asyncio.Event()

    async def train(**kwargs):
        await release.wait()
        return RESULT

    trainer.side_effect = train
    for _ in range(3):
        await _submit()
    worker = MLJobWorker(redis, max_concurrent=2)

    assert await worker.run_once() == 2
    assert await worker.run_once() == 0
    release.set()
    await asyncio.gather(*worker._active.values())
    assert await worker.run_once() == 1
    await asyncio.gather(*worker._active.values())


# ── Waiting ─────────────────────────────────────────────────────────────────


async def test_caller_receives_the_workers_result(queue, trainer):
    _, redis, _, _ = queue
    job_id = await _submit()
    waiting = asyncio.create_task(jobs.wait_for_training(job_id, timeout_seconds=5))

    await _run_one(redis)

    assert await waiting == RESULT


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("invalid_request", ValueError),
        ("session_expired", PermissionError),
        ("failed", RuntimeError),
        ("worker_interrupted", RuntimeError),
    ],
)
async def test_worker_failures_surface_as_the_error_a_local_run_would_raise(queue, kind, error):
    repo, _, _, _ = queue
    job_id = await _submit()
    repo.rows[job_id].update(status="failed", error_kind=kind, error_message="why")

    with pytest.raises(error, match="why"):
        await jobs.wait_for_training(job_id, timeout_seconds=5)


async def test_unclaimed_job_is_withdrawn_so_no_late_worker_trains_it(monkeypatch, queue, trainer):
    repo, redis, _, _ = queue
    monkeypatch.setattr(settings, "ML_JOB_QUEUE_TIMEOUT_SECONDS", 0.01)
    job_id = await _submit()

    with pytest.raises(MLJobUnavailable, match="nova-worker"):
        await jobs.wait_for_training(job_id, timeout_seconds=5)

    assert repo.rows[job_id]["status"] == "failed"
    assert await MLJobWorker(redis).run_once() == 0
    trainer.assert_not_awaited()


async def test_caller_that_goes_away_cancels_its_job(queue):
    repo, _, _, _ = queue
    job_id = await _submit()
    repo.rows[job_id]["status"] = "running"
    waiting = asyncio.create_task(jobs.wait_for_training(job_id, timeout_seconds=30))
    await asyncio.sleep(0.01)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert repo.rows[job_id]["cancel_requested"] is True


async def test_overall_timeout_cancels_the_job(queue):
    repo, _, _, _ = queue
    job_id = await _submit()
    repo.rows[job_id]["status"] = "running"

    with pytest.raises(MLJobUnavailable, match="timed out"):
        await jobs.wait_for_training(job_id, timeout_seconds=0.01)

    assert repo.rows[job_id]["cancel_requested"] is True


# ── Dispatch ────────────────────────────────────────────────────────────────

TRAIN = dict(
    model_name="churn",
    model_type="classification",
    algorithm="auto",
    training_sql=SQL,
    target_column="churned",
    feature_columns=["tenure"],
    hyperparameters=None,
    test_size=0.2,
    database_name="analytics",
    username="alice",
    password="alice-password",
    role="analyst",
    security_context_version=4,
)


async def test_training_goes_to_the_worker_only_when_configured_and_session_bound(
    monkeypatch, queue, trainer
):
    from app.modules.ml_engine.service import ml_engine_service

    repo, redis, _, _ = queue
    monkeypatch.setattr(settings, "ML_TRAINING_EXECUTOR", "worker")
    request = asyncio.create_task(ml_engine_service.train_model(**TRAIN, session_id="session-1"))
    await asyncio.sleep(0.01)

    # Nothing trained in the API process; a job is waiting for the worker.
    trainer.assert_not_awaited()
    (job,) = repo.rows.values()
    assert job["status"] == "queued" and "alice-password" not in json.dumps(job)

    await _run_one(redis)
    assert await request == RESULT


@pytest.mark.parametrize(("executor", "session_id"), [("local", "session-1"), ("worker", None)])
async def test_training_stays_local_without_the_worker_or_without_a_session(
    monkeypatch, queue, trainer, executor, session_id
):
    from app.modules.ml_engine.service import ml_engine_service

    repo, _, _, _ = queue
    monkeypatch.setattr(settings, "ML_TRAINING_EXECUTOR", executor)

    assert await ml_engine_service.train_model(**TRAIN, session_id=session_id) == RESULT

    assert repo.rows == {}
    assert trainer.await_args.kwargs["password"] == "alice-password"


def test_training_runs_locally_by_default():
    from app.core.config import Settings

    assert Settings.model_fields["ML_TRAINING_EXECUTOR"].default == "local"


# ── Status route ────────────────────────────────────────────────────────────


@pytest.fixture
def status_user():
    return {**USER, "session_id": "session-1"}


async def test_job_status_is_shown_to_the_session_that_queued_it(queue, status_user):
    from app.modules.ml_engine.router import training_job

    job_id = await _submit()

    status = await training_job(job_id, status_user)

    assert (status["job_id"], status["status"], status["model_name"]) == (job_id, "queued", "churn")
    assert "encrypted_sql" not in status and "request_json" not in status


@pytest.mark.parametrize(
    "other",
    [
        {"username": "mallory"},
        {"active_role": "admin"},
        {"security_context_version": 5},
        {"session_id": "session-2"},
    ],
)
async def test_job_status_is_hidden_from_anyone_else(queue, status_user, other):
    from app.modules.ml_engine.router import training_job

    job_id = await _submit()

    with pytest.raises(HTTPException) as refused:
        await training_job(job_id, {**status_user, **other})

    assert refused.value.status_code == 404


async def test_unknown_job_is_indistinguishable_from_someone_elses(queue, status_user):
    from app.modules.ml_engine.router import training_job

    with pytest.raises(HTTPException) as refused:
        await training_job("no-such-job", status_user)

    assert refused.value.status_code == 404
