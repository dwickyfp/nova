"""Durable migration jobs and the short-lived link to the caller's session."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from pydantic import BaseModel

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.database import db

from .schemas import MigrationJobAccepted, MigrationJobStatus

logger = logging.getLogger(__name__)

_TABLE = "NOVA_SYSTEM.CONFIG_MIGRATION_JOBS"
_COLUMNS = (
    "id, operation, actor, session_fingerprint, active_role, "
    "security_context_version, source_name, "
    "request_json, status, current_database, result_json, error_code, "
    "created_at, started_at, heartbeat_at, finished_at"
)
_SESSION_KEY_PREFIX = "nova:migration:job-session:"
_TERMINAL = frozenset({"succeeded", "partial", "failed", "interrupted"})


class MigrationJobUnavailable(RuntimeError):
    """The standalone worker did not return a result within the API wait bound."""

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class MigrationJobFailed(RuntimeError):
    _STATUS = {
        "unknown_source": 404,
        "source_unavailable": 502,
        "preflight_failed": 409,
        "data_movement_failed": 422,
        "confirmation_required": 422,
        "execute_disabled": 403,
        "session_expired": 401,
    }

    def __init__(self, code: str) -> None:
        self.code = code
        self.status_code = self._STATUS.get(code, 500)
        super().__init__(f"Migration operation failed ({code})")


class MigrationJobRepository:
    @staticmethod
    def _row(values: tuple[Any, ...] | list[Any]) -> dict[str, Any]:
        return dict(zip((name.strip() for name in _COLUMNS.split(",")), values, strict=True))

    async def create(
        self,
        *,
        job_id: str,
        operation: str,
        actor: str,
        session_fingerprint: str,
        role: str | None,
        security_context_version: int,
        source: str,
        request: dict[str, Any],
    ) -> None:
        await db.execute_system(
            f"INSERT INTO {_TABLE} (id, operation, actor, session_fingerprint, active_role, "
            "security_context_version, source_name, request_json, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', NOW())",
            [
                job_id,
                operation,
                actor,
                session_fingerprint,
                role,
                security_context_version,
                source,
                json.dumps(request),
            ],
        )

    async def get(self, job_id: str) -> dict[str, Any] | None:
        result = await db.execute_system(f"SELECT {_COLUMNS} FROM {_TABLE} WHERE id = %s", [job_id])
        return self._row(result["rows"][0]) if result["rows"] else None

    async def queued(self, limit: int = 4) -> list[dict[str, Any]]:
        result = await db.execute_system(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE status = 'queued' "
            "ORDER BY CASE WHEN operation IN ('execute', 'execute_batch') "
            "THEN 1 ELSE 0 END, created_at, id LIMIT %s",
            [limit],
        )
        return [self._row(row) for row in result["rows"]]

    async def claim(self, job_id: str) -> bool:
        result = await db.execute_system(
            f"UPDATE {_TABLE} SET status = 'running', started_at = NOW(), "
            "heartbeat_at = NOW() WHERE id = %s AND status = 'queued'",
            [job_id],
        )
        return bool(result.get("affected"))

    async def heartbeat(self, job_id: str) -> None:
        await db.execute_system(
            f"UPDATE {_TABLE} SET heartbeat_at = NOW() WHERE id = %s AND status = 'running'",
            [job_id],
        )

    async def progress(
        self, job_id: str, *, current_database: str | None, result: dict[str, Any]
    ) -> None:
        safe = SanitizingJSONResponse._sanitize(result)
        await db.execute_system(
            f"UPDATE {_TABLE} SET current_database = %s, result_json = %s, "
            "heartbeat_at = NOW() WHERE id = %s AND status = 'running'",
            [current_database, json.dumps(safe), job_id],
        )

    async def finish(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        if status not in _TERMINAL:
            raise ValueError("migration job must finish in a terminal state")
        safe = SanitizingJSONResponse._sanitize(result) if result is not None else None
        await db.execute_system(
            f"UPDATE {_TABLE} SET status = %s, current_database = NULL, "
            "result_json = COALESCE(%s, result_json), error_code = %s, finished_at = NOW(), "
            "heartbeat_at = NOW() WHERE id = %s AND status = 'running'",
            [status, json.dumps(safe) if safe is not None else None, error_code, job_id],
        )

    async def interrupt_stale(self, *, older_than_seconds: int = 120) -> None:
        await db.execute_system(
            f"UPDATE {_TABLE} SET status = 'interrupted', current_database = NULL, "
            "error_code = 'worker_interrupted', finished_at = NOW() "
            "WHERE status = 'running' AND heartbeat_at < "
            "DATE_SUB(NOW(), INTERVAL %s SECOND)",
            [older_than_seconds],
        )

    async def delete(self, job_id: str) -> None:
        await db.execute_system(f"DELETE FROM {_TABLE} WHERE id = %s", [job_id])

    async def prune_completed_reads(self) -> None:
        await db.execute_system(
            f"DELETE FROM {_TABLE} WHERE operation NOT IN ('execute', 'execute_batch') "
            "AND status IN ('succeeded', 'partial', 'failed', 'interrupted') "
            "AND finished_at < DATE_SUB(NOW(), INTERVAL 1 DAY)"
        )


migration_job_repo = MigrationJobRepository()


def session_fingerprint(session_id: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), session_id.encode(), hashlib.sha256).hexdigest()


async def submit_job(
    operation: str,
    body: BaseModel,
    user: dict[str, Any],
) -> MigrationJobAccepted:
    """Queue only references and request options; never persist a credential."""
    session_id = user.get("session_id")
    if not session_id:
        raise MigrationJobUnavailable("A live session is required to queue a migration job")
    job_id = str(uuid4())
    key = f"{_SESSION_KEY_PREFIX}{job_id}"
    await write_audit_log(
        event_type="migration",
        user_name=user["username"],
        action="queue_" + operation,
        object_type="migration_job",
        object_name=job_id,
        status="requested",
    )
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await client.set(key, session_id, ex=settings.SESSION_TTL_SECONDS)
        try:
            await migration_job_repo.create(
                job_id=job_id,
                operation=operation,
                actor=user["username"],
                session_fingerprint=session_fingerprint(session_id),
                role=user.get("active_role"),
                security_context_version=int(user.get("security_context_version") or 1),
                source=body.source,
                request=body.model_dump(mode="json"),
            )
        except Exception:
            await client.delete(key)
            raise
    finally:
        await client.aclose()
    databases = getattr(body, "databases", None)
    if databases is None:
        database = getattr(body, "database", None)
        databases = [database] if database else []
    return MigrationJobAccepted(
        job_id=job_id,
        status="queued",
        source=body.source,
        databases=databases,
    )


async def wait_for_job(job_id: str, *, timeout_seconds: float = 60) -> dict[str, Any]:
    """Wait for a read operation while all source/target work stays in worker."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        try:
            job = await migration_job_repo.get(job_id)
        except Exception as exc:
            logger.warning(
                "migration job status read failed exception=%s.%s",
                type(exc).__module__,
                type(exc).__qualname__,
            )
            raise MigrationJobUnavailable(
                "Migration job status is temporarily unavailable"
            ) from None
        if job is None:
            raise MigrationJobUnavailable("Migration job was not found")
        if job["status"] == "succeeded":
            result = json.loads(job["result_json"] or "{}")
            try:
                await migration_job_repo.delete(job_id)
            except Exception as exc:
                logger.warning(
                    "migration read job cleanup failed exception=%s.%s",
                    type(exc).__module__,
                    type(exc).__qualname__,
                )
            return result
        if job["status"] in _TERMINAL:
            code = job["error_code"] or job["status"]
            raise MigrationJobFailed(code)
        await asyncio.sleep(0.25)
    raise MigrationJobUnavailable(f"Migration worker timed out; job_id={job_id}")


def public_status(job: dict[str, Any]) -> MigrationJobStatus:
    request = json.loads(job["request_json"] or "{}")
    payload = json.loads(job["result_json"] or "{}")
    databases = request.get("databases")
    if databases is None:
        databases = [request["database"]] if request.get("database") else []
    return MigrationJobStatus(
        job_id=job["id"],
        source=job["source_name"],
        databases=databases,
        status=job["status"],
        error_code=job["error_code"],
        current_database=job["current_database"],
        results=payload.get("results", []),
        created_at=_date(job["created_at"]),
        started_at=_date(job["started_at"]),
        finished_at=_date(job["finished_at"]),
    )


def _date(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


async def job_session_id(client: aioredis.Redis, job_id: str) -> str | None:
    return await client.get(f"{_SESSION_KEY_PREFIX}{job_id}")


async def clear_job_session(client: aioredis.Redis, job_id: str) -> None:
    await client.delete(f"{_SESSION_KEY_PREFIX}{job_id}")
