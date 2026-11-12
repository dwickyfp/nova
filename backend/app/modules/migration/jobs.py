"""Durable migration jobs and the short-lived link to the caller's session."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

import redis.asyncio as aioredis

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.metadata_runtime import metadata_db
from app.modules.access_control.security_context import SecurityContext, SecurityContextError

from .metadata_repository import MigrationMetadataRepository
from .schemas import MigrationJobAccepted, MigrationJobStatus

logger = logging.getLogger(__name__)

_SESSION_KEY_PREFIX = "nova:migration:job-session:"
_TERMINAL = frozenset({"succeeded", "partial", "failed", "interrupted"})

#: The standalone migration worker. It claims, advances, interrupts and prunes
#: jobs through the ingestion workload; the work itself still runs as the
#: person who queued the job, from their live session.
MIGRATION_WORKER = SecurityContext("migration-worker", "nova_service", principal_type="SERVICE")


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
        "session_expired": 401,
    }

    def __init__(self, code: str) -> None:
        self.code = code
        self.status_code = self._STATUS.get(code, 500)
        super().__init__(f"Migration operation failed ({code})")


class MigrationLeaseLost(RuntimeError):
    """The worker no longer holds the lease generation it claimed the job under."""


def request_actor(user: dict[str, Any]) -> SecurityContext:
    """The bound identity of the person behind an API request."""
    security = user.get("security")
    return security if isinstance(security, SecurityContext) else SecurityContext.from_session(user)


class MigrationJobRepository:
    """Migration jobs in PostgreSQL, one actor-bound transaction per operation.

    People queue, read and clean up only their own jobs. The worker acts as
    ``MIGRATION_WORKER``: it claims a queued job under a new lease generation and
    names that generation on every later heartbeat, progress and outcome write,
    so a worker that lost its claim cannot change the job any more.
    """

    @staticmethod
    @asynccontextmanager
    async def _repository(
        actor: SecurityContext, *, read_only: bool = False
    ) -> AsyncIterator[MigrationMetadataRepository]:
        async with metadata_db().transaction(actor, read_only=read_only) as conn:
            yield MigrationMetadataRepository(conn, actor)

    # ── The person who queued the job ─────────────────────────────────────

    async def create(
        self,
        actor: SecurityContext,
        *,
        job_id: str,
        operation: str,
        session_fingerprint: str,
        source: str,
        request: dict[str, Any],
    ) -> None:
        async with self._repository(actor) as repository:
            await repository.create_job(
                job_id=job_id,
                operation=operation,
                session_fingerprint=session_fingerprint,
                source=source,
                request=request,
            )

    async def get(self, actor: SecurityContext, job_id: str) -> dict[str, Any] | None:
        async with self._repository(actor, read_only=True) as repository:
            return await repository.get_job(job_id)

    async def delete(self, actor: SecurityContext, job_id: str) -> bool:
        async with self._repository(actor) as repository:
            return await repository.delete_finished_job(job_id)

    # ── The migration worker ──────────────────────────────────────────────

    async def queued(self, limit: int = 4) -> list[dict[str, Any]]:
        async with self._repository(MIGRATION_WORKER, read_only=True) as repository:
            return await repository.queued(limit)

    async def claim(self, job_id: str) -> int | None:
        """Start a queued job; returns the lease generation, or None if another won."""
        async with self._repository(MIGRATION_WORKER) as repository:
            return await repository.claim(job_id)

    async def heartbeat(self, job_id: str, *, generation: int) -> bool:
        async with self._repository(MIGRATION_WORKER) as repository:
            return await repository.heartbeat(job_id, generation=generation)

    async def progress(
        self,
        job_id: str,
        *,
        generation: int,
        current_database: str | None,
        result: dict[str, Any],
    ) -> bool:
        async with self._repository(MIGRATION_WORKER) as repository:
            return await repository.progress(
                job_id, generation=generation, current_database=current_database, result=result
            )

    async def finish(
        self,
        job_id: str,
        *,
        generation: int,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        if status not in _TERMINAL:
            raise ValueError("migration job must finish in a terminal state")
        async with self._repository(MIGRATION_WORKER) as repository:
            return await repository.finish(
                job_id, generation=generation, status=status, result=result,
                error_code=error_code,
            )

    async def interrupt_stale(self, *, older_than_seconds: int = 120) -> list[str]:
        async with self._repository(MIGRATION_WORKER) as repository:
            return await repository.interrupt_stale(older_than_seconds=older_than_seconds)

    async def prune_completed_reads(self) -> int:
        async with self._repository(MIGRATION_WORKER) as repository:
            return await repository.prune_completed_reads()


migration_job_repo = MigrationJobRepository()


def session_fingerprint(session_id: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), session_id.encode(), hashlib.sha256).hexdigest()


class MigrationJobRequest(Protocol):
    @property
    def source(self) -> str: ...

    def model_dump(self, *, mode: Literal["json"]) -> dict[str, Any]: ...


async def submit_job(
    operation: str,
    body: MigrationJobRequest,
    user: dict[str, Any],
) -> MigrationJobAccepted:
    """Queue only references and request options; never persist a credential."""
    session_id = user.get("session_id")
    if not session_id:
        raise MigrationJobUnavailable("A live session is required to queue a migration job")
    try:
        actor = request_actor(user)
    except SecurityContextError:
        raise MigrationJobUnavailable(
            "A session with an active role is required to queue a migration job", 401
        ) from None
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
                actor,
                job_id=job_id,
                operation=operation,
                session_fingerprint=session_fingerprint(session_id),
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


async def wait_for_job(
    job_id: str, actor: SecurityContext, *, timeout_seconds: float = 60
) -> dict[str, Any]:
    """Wait for a read operation while all source/target work stays in worker."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        try:
            job = await migration_job_repo.get(actor, job_id)
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
                await migration_job_repo.delete(actor, job_id)
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
    value = await client.get(f"{_SESSION_KEY_PREFIX}{job_id}")
    return value.decode() if isinstance(value, bytes) else value


async def clear_job_session(client: aioredis.Redis, job_id: str) -> None:
    await client.delete(f"{_SESSION_KEY_PREFIX}{job_id}")
