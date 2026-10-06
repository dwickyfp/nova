"""The worker side of ML training jobs: claim, verify the session, train."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.redis import session_store
from app.core.security import decrypt_password
from app.modules.ml_engine.jobs import (
    clear_job_session,
    job_session_id,
    ml_job_repo,
    training_sql,
)
from app.modules.query.sql_pipeline import redact_for_output

logger = logging.getLogger(__name__)


class MLJobSessionExpired(PermissionError):
    """The session the job was queued from is gone or no longer the same."""


class MLJobWorker:
    def __init__(
        self,
        client: aioredis.Redis,
        *,
        max_concurrent: int | None = None,
        heartbeat_interval: float = 5,
        poll_interval: float = 0.5,
    ) -> None:
        self._client = client
        self._max_concurrent = max_concurrent or settings.ML_WORKER_PROCESSES
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        self._active: dict[str, asyncio.Task[None]] = {}

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("nova-worker ML training queue started")
        next_reconcile = 0.0
        while not stop_event.is_set():
            try:
                self._reap()
                if asyncio.get_running_loop().time() >= next_reconcile:
                    await ml_job_repo.interrupt_stale()
                    await ml_job_repo.prune_finished()
                    next_reconcile = asyncio.get_running_loop().time() + 30
                processed = await self.run_once()
            except Exception as exc:
                logger.error("ML job poll failed: %s", type(exc).__name__)
                processed = 0
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
        for task in self._active.values():
            task.cancel()
        await asyncio.gather(*self._active.values(), return_exceptions=True)

    def _reap(self) -> None:
        for job_id, task in list(self._active.items()):
            if task.done():
                del self._active[job_id]

    async def run_once(self) -> int:
        self._reap()
        capacity = self._max_concurrent - len(self._active)
        if capacity <= 0:
            return 0
        processed = 0
        for job in await ml_job_repo.queued(limit=capacity):
            if not await ml_job_repo.claim(job["id"]):
                continue
            processed += 1
            self._active[job["id"]] = asyncio.create_task(
                self.process_claimed(job), name=f"nova-ml-job-{job['id']}"
            )
        return processed

    async def _session(self, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """The caller's session, or refuse: never a substitute identity."""
        session_id = await job_session_id(self._client, str(job["id"]))
        session = await session_store.get(session_id) if session_id else None
        if (
            not session
            or not session.get("encrypted_password")
            or session.get("username") != job["actor"]
            or session.get("active_role") != job["active_role"]
            or int(session.get("security_context_version") or 1)
            != int(job["security_context_version"])
        ):
            raise MLJobSessionExpired("The session that requested this training has ended")
        return session_id, session

    async def _heartbeat(
        self, job_id: str, session_id: str, training: asyncio.Task, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_interval)
            if stop.is_set():
                return
            try:
                if await ml_job_repo.heartbeat(job_id):
                    training.cancel()
                    return
                await session_store.refresh(session_id)
            except Exception as exc:
                logger.error("ML job heartbeat failed: %s", type(exc).__name__)

    async def process_claimed(self, job: dict[str, Any]) -> None:
        from app.modules.ml_engine.service import ml_engine_service

        job_id = str(job["id"])
        stop_heartbeat = asyncio.Event()
        heartbeat: asyncio.Task[None] | None = None
        training: asyncio.Task | None = None
        status, outcome = "failed", {"error_kind": "failed", "error_message": "Training failed"}
        try:
            session_id, session = await self._session(job)
            request = json.loads(job["request_json"])
            training = asyncio.create_task(
                ml_engine_service.train_model_locally(
                    training_sql=training_sql(job),
                    username=job["actor"],
                    password=decrypt_password(session["encrypted_password"]),
                    role=job["active_role"],
                    security_context_version=int(job["security_context_version"]),
                    tenant=job.get("tenant") or "default",
                    created_by=job["actor"],
                    **request,
                )
            )
            heartbeat = asyncio.create_task(
                self._heartbeat(job_id, session_id, training, stop_heartbeat)
            )
            status, outcome = "succeeded", {"result": await training}
        except asyncio.CancelledError:
            status = "cancelled"
            outcome = {"error_kind": "cancelled", "error_message": "Training was cancelled"}
            if asyncio.current_task().cancelling():
                # The worker itself is stopping, not the caller cancelling.
                status = "interrupted"
                await self._finish(job, status, outcome)
                raise
        except MLJobSessionExpired as exc:
            outcome = {"error_kind": "session_expired", "error_message": str(exc)}
        except ValueError as exc:
            outcome = {
                "error_kind": "invalid_request",
                "error_message": redact_for_output(str(exc)),
            }
        except Exception as exc:
            logger.error("ML job %s failed: %s", job_id, type(exc).__name__)
            outcome = {
                "error_kind": "failed",
                "error_message": f"Training failed: {redact_for_output(str(exc))}",
            }
        finally:
            stop_heartbeat.set()
            if training is not None and not training.done():
                # Stopping the worker must stop the child process it started.
                training.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await training
            if heartbeat is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            await clear_job_session(self._client, job_id)
        await self._finish(job, status, outcome)

    async def _finish(self, job: dict[str, Any], status: str, outcome: dict[str, Any]) -> None:
        await ml_job_repo.finish(str(job["id"]), status=status, **outcome)
        with contextlib.suppress(Exception):
            await write_audit_log(
                event_type="ml",
                user_name=job["actor"],
                action="train",
                object_type="ml_job",
                object_name=str(job["id"]),
                status="SUCCESS" if status == "succeeded" else "ERROR",
                error_message=outcome.get("error_message"),
                active_role=job["active_role"],
                security_context_version=int(job["security_context_version"]),
            )
