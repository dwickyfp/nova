"""Migration operations run only in the standalone nova-worker process."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.redis import session_store
from app.observability.metrics import (
    MIGRATION_WORKER_ACTIVE_JOBS,
    MIGRATION_WORKER_JOB_DURATION,
    MIGRATION_WORKER_JOBS,
    MIGRATION_WORKER_POLL_ERRORS,
)

from .data_mover import DataMovementError
from .jobs import (
    clear_job_session,
    job_session_id,
    migration_job_repo,
)
from .schemas import (
    DatabasesRequest,
    DryRunRequest,
    EnumerateRequest,
    ExecuteBatchRequest,
    ExecuteRequest,
    PlanRequest,
    PreflightRequest,
    SourceConnectionTestRequest,
)
from .service import (
    MigrationExecuteConfirmationError,
    MigrationExecuteGateError,
    MigrationPreflightError,
    migration_service,
)
from .source import SourceConnectionError

logger = logging.getLogger(__name__)

_METRIC_OPERATIONS = frozenset(
    {
        "engine",
        "test_source",
        "databases",
        "enumerate",
        "dry_run",
        "plan",
        "preflight",
        "execute",
        "execute_batch",
    }
)
_METRIC_STATUSES = frozenset({"succeeded", "partial", "failed", "interrupted"})


def _metric_operation(operation: Any) -> str:
    return operation if isinstance(operation, str) and operation in _METRIC_OPERATIONS else "other"


def _numeric_errno(exc: Exception) -> int | None:
    first = exc.args[0] if exc.args else None
    return first if type(first) is int else None


class MigrationSessionExpired(RuntimeError):
    pass


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, MigrationSessionExpired):
        return "session_expired"
    if isinstance(exc, MigrationExecuteGateError):
        return "execute_disabled"
    if isinstance(exc, MigrationExecuteConfirmationError):
        return "confirmation_required"
    if isinstance(exc, MigrationPreflightError):
        return "preflight_failed"
    if isinstance(exc, DataMovementError):
        return "data_movement_failed"
    if isinstance(exc, SourceConnectionError):
        if str(exc).startswith("Unknown migration source"):
            return "unknown_source"
        return "source_unavailable"
    return "migration_failed"


class MigrationJobWorker:
    def __init__(
        self,
        client: aioredis.Redis,
        *,
        max_concurrent: int = 4,
        heartbeat_interval: float = 5,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        self._client = client
        self._max_concurrent = max_concurrent
        self._heartbeat_interval = heartbeat_interval
        self._active: dict[str, asyncio.Task[None]] = {}
        self._active_operations: dict[str, str] = {}

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("nova-worker migration queue started")
        next_reconcile = 0.0
        while not stop_event.is_set():
            try:
                self._reap()
                if asyncio.get_running_loop().time() >= next_reconcile:
                    try:
                        await migration_job_repo.interrupt_stale()
                        await migration_job_repo.prune_completed_reads()
                    except Exception:
                        MIGRATION_WORKER_POLL_ERRORS.labels(phase="reconcile").inc()
                        raise
                    next_reconcile = asyncio.get_running_loop().time() + 30
                processed = await self.run_once()
            except Exception as exc:
                logger.error("migration worker poll failed: %s", type(exc).__name__)
                processed = 0
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
        if self._active:
            done, pending = await asyncio.wait(
                self._active.values(),
                timeout=max(0.0, settings.WORKER_SHUTDOWN_GRACE_SECONDS),
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled() and (error := task.exception()):
                    logger.error("migration worker task failed: %s", type(error).__name__)

    def _reap(self) -> None:
        for job_id, task in list(self._active.items()):
            if not task.done():
                continue
            del self._active[job_id]
            self._active_operations.pop(job_id, None)
            if not task.cancelled() and (error := task.exception()):
                logger.error("migration job %s task failed: %s", job_id, type(error).__name__)

    async def run_once(self) -> int:
        self._reap()
        processed = 0
        capacity = self._max_concurrent - len(self._active)
        if capacity <= 0:
            return 0
        active_writes = sum(
            operation in {"execute", "execute_batch"}
            for operation in self._active_operations.values()
        )
        try:
            queued = await migration_job_repo.queued(limit=max(20, capacity))
        except Exception:
            MIGRATION_WORKER_POLL_ERRORS.labels(phase="poll").inc()
            raise
        for job in queued:
            if processed >= capacity:
                break
            if job["operation"] in {"execute", "execute_batch"} and active_writes >= 1:
                continue
            try:
                claimed = await migration_job_repo.claim(job["id"])
            except Exception:
                MIGRATION_WORKER_POLL_ERRORS.labels(phase="claim").inc()
                raise
            if not claimed:
                continue
            processed += 1
            if job["operation"] in {"execute", "execute_batch"}:
                active_writes += 1
            self._active[job["id"]] = asyncio.create_task(
                self.process_claimed(job), name=f"nova-migration-{job['id']}"
            )
            self._active_operations[job["id"]] = job["operation"]
        return processed

    async def process_claimed(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        operation = _metric_operation(job.get("operation"))
        started = time.perf_counter()
        finished_status: str | None = None
        MIGRATION_WORKER_ACTIVE_JOBS.inc()
        stop_heartbeat = asyncio.Event()
        heartbeat: asyncio.Task[None] | None = None
        try:
            session_id, session = await self._session(job)
            heartbeat = asyncio.create_task(self._heartbeat(job_id, session_id, stop_heartbeat))
            payload = json.loads(job["request_json"])
            if job["operation"] in {"execute", "execute_batch"}:
                status, result = await self._execute(job, payload, session_id, session)
            else:
                result = await self._read(job, payload, session_id, session)
                status = "succeeded"
            await migration_job_repo.finish(job_id, status=status, result=result)
            finished_status = status
            await self._audit_safely(job, status)
        except asyncio.CancelledError:
            await migration_job_repo.finish(
                job_id, status="interrupted", error_code="worker_interrupted"
            )
            finished_status = "interrupted"
            await self._audit_safely(job, "interrupted")
            raise
        except Exception as exc:
            code = _safe_error(exc)
            logger.error(
                "migration job %s operation=%s failed code=%s exception=%s.%s errno=%s",
                job_id,
                operation,
                code,
                type(exc).__module__,
                type(exc).__qualname__,
                _numeric_errno(exc),
            )
            await migration_job_repo.finish(job_id, status="failed", error_code=code)
            finished_status = "failed"
            await self._audit_safely(job, "failed")
        finally:
            try:
                stop_heartbeat.set()
                if heartbeat is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
                await clear_job_session(self._client, job_id)
            finally:
                metric_status = (
                    finished_status if finished_status in _METRIC_STATUSES else "unfinished"
                )
                MIGRATION_WORKER_JOBS.labels(operation=operation, status=metric_status).inc()
                MIGRATION_WORKER_JOB_DURATION.labels(
                    operation=operation, status=metric_status
                ).observe(time.perf_counter() - started)
                MIGRATION_WORKER_ACTIVE_JOBS.dec()

    async def _heartbeat(self, job_id: str, session_id: str, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._heartbeat_interval)
            if stop_event.is_set():
                return
            try:
                await migration_job_repo.heartbeat(job_id)
            except Exception as exc:
                logger.error("migration heartbeat %s failed: %s", job_id, type(exc).__name__)
            try:
                await session_store.refresh(session_id)
            except Exception as exc:
                logger.error("migration session refresh failed: %s", type(exc).__name__)

    async def _session(
        self, job: dict[str, Any], known_session_id: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        session_id = known_session_id or await job_session_id(self._client, str(job["id"]))
        if not session_id:
            raise MigrationSessionExpired()
        session = await session_store.get(session_id)
        if not session or not session.get("encrypted_password"):
            raise MigrationSessionExpired()
        if session.get("username") != job["actor"]:
            raise MigrationSessionExpired()
        if session.get("active_role") != job["active_role"]:
            raise MigrationSessionExpired()
        if int(session.get("security_context_version") or 1) != int(
            job["security_context_version"]
        ):
            raise MigrationSessionExpired()
        return session_id, session

    async def _read(
        self,
        job: dict[str, Any],
        payload: dict[str, Any],
        session_id: str,
        session: dict[str, Any],
    ) -> dict[str, Any]:
        operation = job["operation"]
        actor = job["actor"]
        result: BaseModel | dict[str, Any]
        if operation == "engine":
            result = await migration_service.engine_status()
        elif operation == "test_source":
            test_source_request = SourceConnectionTestRequest.model_validate(payload)
            result = await migration_service.test_source_connection(
                name=test_source_request.source,
                host=test_source_request.host,
                port=test_source_request.port,
                username=test_source_request.username,
                secret_ref=test_source_request.secret_ref,
            )
        elif operation == "databases":
            databases_request = DatabasesRequest.model_validate(payload)
            result = await migration_service.databases(databases_request.source)
        elif operation == "enumerate":
            enumerate_request = EnumerateRequest.model_validate(payload)
            result = await migration_service.enumerate(
                enumerate_request.source, enumerate_request.database
            )
        elif operation == "dry_run":
            dry_run_request = DryRunRequest.model_validate(payload)
            result = await migration_service.dry_run(
                dry_run_request.source,
                dry_run_request.database,
                dry_run_request.objects,
                actor=actor,
            )
        elif operation == "plan":
            plan_request = PlanRequest.model_validate(payload)
            result = await migration_service.plan(
                plan_request.source,
                plan_request.database,
                target_database=plan_request.target_database,
                objects=plan_request.objects,
                create_database=plan_request.create_database,
                actor=actor,
            )
        elif operation == "preflight":
            preflight_request = PreflightRequest.model_validate(payload)
            result = await migration_service.preflight(
                preflight_request.source,
                preflight_request.database,
                target_database=preflight_request.target_database,
                objects=preflight_request.objects,
                create_database=preflight_request.create_database,
                include_data=preflight_request.include_data,
                actor=actor,
                encrypted_password=session["encrypted_password"],
                session_id=session_id,
                role=job["active_role"],
            )
        else:
            raise ValueError("unknown migration operation")
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        return dict(result)

    async def _execute(
        self,
        job: dict[str, Any],
        payload: dict[str, Any],
        session_id: str,
        session: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        body: ExecuteRequest | ExecuteBatchRequest
        if job["operation"] == "execute":
            body = ExecuteRequest.model_validate(payload)
            databases = [body.database]
        else:
            body = ExecuteBatchRequest.model_validate(payload)
            databases = body.databases
        available = set((await migration_service.databases(body.source)).databases)
        results: list[dict[str, Any]] = []
        for database in databases:
            await migration_job_repo.progress(
                job["id"], current_database=database, result={"results": results}
            )
            try:
                if database not in available:
                    raise SourceConnectionError("Selected database is no longer available")
                session_id, session = await self._session(job, session_id)
                response = await migration_service.execute(
                    body.source,
                    database,
                    target_database=(
                        body.target_database if isinstance(body, ExecuteRequest) else database
                    ),
                    objects=body.objects if isinstance(body, ExecuteRequest) else [],
                    create_database=body.create_database,
                    acknowledge_omissions=body.acknowledge_omissions,
                    confirmation=(
                        body.confirmation if isinstance(body, ExecuteRequest) else database
                    ),
                    actor=job["actor"],
                    encrypted_password=session["encrypted_password"],
                    session_id=session_id,
                    role=job["active_role"],
                    include_data=body.include_data,
                    stage_connection=body.stage_connection,
                )
                data_failures = sum(not item.verified for item in response.data)
                result = {
                    "database": database,
                    "status": "failed" if response.failed or data_failures else "succeeded",
                    "succeeded": response.succeeded,
                    "failed": response.failed + data_failures,
                    "skipped": response.skipped,
                    "rows_moved": response.rows_moved,
                    "error": "data_verification_failed" if data_failures else None,
                    "steps": [
                        {
                            "order": step.order,
                            "kind": step.kind,
                            "object_name": step.object_name,
                            "status": step.status,
                            "error": SanitizingJSONResponse._sanitize(step.error),
                        }
                        for step in response.results
                    ],
                    "data": SanitizingJSONResponse._sanitize(
                        [item.model_dump(mode="json") for item in response.data]
                    ),
                }
            except Exception as exc:
                result = {"database": database, "status": "failed", "error": _safe_error(exc)}
            results.append(result)
            await migration_job_repo.progress(
                job["id"], current_database=None, result={"results": results}
            )
        succeeded = sum(item["status"] == "succeeded" for item in results)
        status = "succeeded" if succeeded == len(results) else "partial" if succeeded else "failed"
        return status, {"results": results}

    @staticmethod
    async def _audit(job: dict[str, Any], status: str) -> None:
        await write_audit_log(
            event_type="migration",
            user_name=job["actor"],
            action="job_" + job["operation"],
            object_type="migration_job",
            object_name=job["id"],
            status=status,
        )

    async def _audit_safely(self, job: dict[str, Any], status: str) -> None:
        try:
            await self._audit(job, status)
        except Exception as exc:
            logger.error("migration job %s audit failed: %s", job["id"], type(exc).__name__)
