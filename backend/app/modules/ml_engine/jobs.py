"""Durable ML training jobs run by the standalone worker.

Training holds CPU and memory for up to minutes, so an API process hands it to
``nova-worker`` and waits for the result. A job carries no credential: it names
the caller's session, and the worker trains only while that exact session is
still live with the same principal, active role and security-context version.
This is the same contract as the migration job queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.database import db
from app.core.security import decrypt_password, encrypt_password
from app.modules.migration.jobs import session_fingerprint

logger = logging.getLogger(__name__)

_TABLE = "NOVA_SYSTEM.CONFIG_ML_JOBS"
_COLUMNS = (
    "id, actor, session_fingerprint, active_role, security_context_version, tenant, "
    "request_json, encrypted_sql, status, cancel_requested, result_json, error_kind, "
    "error_message, created_at, started_at, heartbeat_at, finished_at"
)
_SESSION_KEY_PREFIX = "nova:ml:job-session:"
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
#: The training request without its SQL, which is stored encrypted.
REQUEST_FIELDS = (
    "model_name",
    "model_type",
    "algorithm",
    "target_column",
    "feature_columns",
    "hyperparameters",
    "test_size",
    "database_name",
    "timestamp_column",
    "series_column",
    "horizon",
    "frequency",
    "mode",
)

CONFIG_ML_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_ML_JOBS (
    id                       VARCHAR(64) NOT NULL,
    actor                    VARCHAR(128) NOT NULL,
    session_fingerprint      VARCHAR(64) NOT NULL,
    active_role              VARCHAR(128),
    security_context_version INT NOT NULL DEFAULT "1",
    tenant                   VARCHAR(128) NOT NULL DEFAULT "default",
    request_json             STRING NOT NULL,
    encrypted_sql            STRING NOT NULL,
    status                   VARCHAR(16) NOT NULL,
    cancel_requested         BOOLEAN NOT NULL DEFAULT "false",
    result_json              STRING,
    error_kind               VARCHAR(32),
    error_message            STRING,
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at               DATETIME,
    heartbeat_at             DATETIME,
    finished_at              DATETIME
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class MLJobUnavailable(RuntimeError):
    """No worker produced a result within the wait bound."""


class MLJobSessionRequired(ValueError):
    """Training on the worker needs the caller's live session."""


class MLJobRepository:
    @staticmethod
    def _row(values: tuple[Any, ...] | list[Any]) -> dict[str, Any]:
        return dict(zip((name.strip() for name in _COLUMNS.split(",")), values, strict=True))

    async def ensure_schema(self) -> None:
        await db.execute_system(CONFIG_ML_JOBS_DDL)

    async def create(
        self,
        *,
        job_id: str,
        actor: str,
        fingerprint: str,
        role: str | None,
        security_context_version: int,
        tenant: str,
        request: dict[str, Any],
        encrypted_sql: str,
    ) -> None:
        await db.execute_system(
            f"INSERT INTO {_TABLE} (id, actor, session_fingerprint, active_role, "
            "security_context_version, tenant, request_json, encrypted_sql, status, "
            "cancel_requested, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', "
            "false, NOW())",
            [
                job_id,
                actor,
                fingerprint,
                role,
                security_context_version,
                tenant,
                json.dumps(request),
                encrypted_sql,
            ],
        )

    async def get(self, job_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE id = %s", [job_id])
        return self._row(result["rows"][0]) if result["rows"] else None

    async def queued(self, limit: int) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE status = 'queued' "
            "ORDER BY created_at, id LIMIT %s",
            [limit],
        )
        return [self._row(row) for row in result["rows"]]

    async def claim(self, job_id: str) -> bool:
        result = await db.execute_system(
            f"UPDATE {_TABLE} SET status = 'running', started_at = NOW(), "
            "heartbeat_at = NOW() WHERE id = %s AND status = 'queued' "
            "AND cancel_requested = false",
            [job_id],
        )
        return bool(result.get("affected"))

    async def heartbeat(self, job_id: str) -> bool:
        """Record liveness. Returns whether the caller asked to cancel."""
        await db.execute_system(
            f"UPDATE {_TABLE} SET heartbeat_at = NOW() WHERE id = %s AND status = 'running'",
            [job_id],
        )
        result = await db.execute_system(
            f"SELECT cancel_requested FROM {_TABLE} WHERE id = %s", [job_id]
        )
        return bool(result["rows"] and result["rows"][0][0])

    async def request_cancel(self, job_id: str) -> None:
        await db.execute_system(
            f"UPDATE {_TABLE} SET cancel_requested = true WHERE id = %s "
            "AND status IN ('queued', 'running')",
            [job_id],
        )

    async def finish(
        self,
        job_id: str,
        *,
        status: str,
        expected: str = "running",
        result: dict[str, Any] | None = None,
        error_kind: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        if status not in TERMINAL:
            raise ValueError("ML job must finish in a terminal state")
        safe = SanitizingJSONResponse._sanitize(result) if result is not None else None
        outcome = await db.execute_system(
            f"UPDATE {_TABLE} SET status = %s, result_json = %s, error_kind = %s, "
            "error_message = %s, finished_at = NOW() WHERE id = %s AND status = %s",
            [
                status,
                json.dumps(safe) if safe is not None else None,
                error_kind,
                error_message,
                job_id,
                expected,
            ],
        )
        return bool(outcome.get("affected"))

    async def interrupt_stale(self, *, older_than_seconds: int = 120) -> None:
        await db.execute_system(
            f"UPDATE {_TABLE} SET status = 'interrupted', error_kind = 'worker_interrupted', "
            "finished_at = NOW() WHERE status = 'running' AND heartbeat_at < "
            "DATE_SUB(NOW(), INTERVAL %s SECOND)",
            [older_than_seconds],
        )

    async def prune_finished(self) -> None:
        await db.execute_system(
            f"DELETE FROM {_TABLE} WHERE status IN ('succeeded', 'failed', 'cancelled', "
            "'interrupted') AND finished_at < DATE_SUB(NOW(), INTERVAL 1 DAY)"
        )


ml_job_repo = MLJobRepository()


def training_sql(job: dict[str, Any]) -> str:
    return decrypt_password(job["encrypted_sql"])


async def submit_training(
    *, session_id: str | None, user: dict[str, Any], training_sql: str, request: dict[str, Any]
) -> str:
    """Queue one training request for the worker. Returns the job id.

    ``user`` supplies the principal, active role, security-context version and
    tenant the worker must find unchanged on the session before it reads data.
    """
    if not session_id:
        raise MLJobSessionRequired("Training on the worker requires a live session")
    unknown = set(request) - set(REQUEST_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported training options: {sorted(unknown)}")
    job_id = str(uuid4())
    key = f"{_SESSION_KEY_PREFIX}{job_id}"
    await write_audit_log(
        event_type="ml",
        user_name=user["username"],
        action="queue_training",
        object_type="ml_job",
        object_name=job_id,
        status="requested",
        session_id=session_id,
    )
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await client.set(key, session_id, ex=settings.SESSION_TTL_SECONDS)
        try:
            await ml_job_repo.create(
                job_id=job_id,
                actor=user["username"],
                fingerprint=session_fingerprint(session_id),
                role=user.get("active_role"),
                security_context_version=int(user.get("security_context_version") or 1),
                tenant=user.get("tenant") or "default",
                request=request,
                # The SQL can name storage or carry literals the caller would
                # not want readable in a metadata table.
                encrypted_sql=encrypt_password(training_sql),
            )
        except Exception:
            await client.delete(key)
            raise
    finally:
        await client.aclose()
    return job_id


async def wait_for_training(job_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    """Wait for the worker's result, raising what a local run would have raised."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while True:
            job = await ml_job_repo.get(job_id)
            if job is None:
                raise MLJobUnavailable("ML training job was not found")
            status = job["status"]
            if status == "succeeded":
                return json.loads(job["result_json"] or "{}")
            if status in TERMINAL:
                raise _failure(job)
            waited = loop.time() - started
            if status == "queued" and waited >= settings.ML_JOB_QUEUE_TIMEOUT_SECONDS:
                # Nothing claimed it: withdraw the job so a worker that appears
                # later does not train a model nobody is waiting for.
                if await ml_job_repo.finish(
                    job_id,
                    status="failed",
                    expected="queued",
                    error_kind="worker_unavailable",
                    error_message="No ML worker picked up the training job",
                ):
                    raise MLJobUnavailable(
                        "No ML worker picked up the training job; is nova-worker running?"
                    )
                continue
            if waited >= timeout_seconds:
                await ml_job_repo.request_cancel(job_id)
                raise MLJobUnavailable(f"ML training timed out waiting for the worker ({job_id})")
            await asyncio.sleep(settings.ML_JOB_POLL_SECONDS)
    except asyncio.CancelledError:
        # The caller went away; stop the training it started.
        await asyncio.shield(ml_job_repo.request_cancel(job_id))
        raise


def _failure(job: dict[str, Any]) -> Exception:
    message = job.get("error_message") or f"ML training {job['status']}"
    if job.get("error_kind") == "invalid_request":
        return ValueError(message)
    if job.get("error_kind") == "session_expired":
        return PermissionError(message)
    return RuntimeError(message)


def public_status(job: dict[str, Any]) -> dict[str, Any]:
    request = json.loads(job["request_json"] or "{}")
    return {
        "job_id": job["id"],
        "status": job["status"],
        "model_name": request.get("model_name"),
        "mode": request.get("mode"),
        "error": job.get("error_message"),
        "result": json.loads(job["result_json"]) if job.get("result_json") else None,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


async def job_session_id(client: aioredis.Redis, job_id: str) -> str | None:
    return await client.get(f"{_SESSION_KEY_PREFIX}{job_id}")


async def clear_job_session(client: aioredis.Redis, job_id: str) -> None:
    await client.delete(f"{_SESSION_KEY_PREFIX}{job_id}")
