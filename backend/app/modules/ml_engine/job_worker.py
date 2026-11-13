"""The worker side of ML training jobs: claim, verify the session, train."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.metadata_scope import acting_as_sync
from app.core.redis import session_store
from app.core.security import decrypt_password
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.ml_engine.jobs import (
    ClaimedJob,
    LocalOutcome,
    clear_job_session,
    job_session_id,
    ml_job_repo,
    session_fingerprint,
    training_sql,
)

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
        self._max_concurrent = (
            settings.ML_WORKER_PROCESSES if max_concurrent is None else max_concurrent
        )
        if self._max_concurrent < 1 or heartbeat_interval <= 0 or poll_interval <= 0:
            raise ValueError("ML worker concurrency and intervals must be positive")
        self._holder = uuid4()
        self._lease_seconds = max(30, int(heartbeat_interval * 3))
        self._heartbeat_interval = heartbeat_interval
        self._poll_interval = poll_interval
        self._active: dict[str, asyncio.Task[None]] = {}

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("nova-worker ML training queue started")
        next_reconcile = 0.0
        try:
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
        finally:
            for task in self._active.values():
                task.cancel()
            await asyncio.gather(*self._active.values(), return_exceptions=True)
            self._active.clear()

    def _reap(self) -> None:
        for job_id, task in list(self._active.items()):
            if task.done():
                if not task.cancelled() and task.exception() is not None:
                    logger.error("ML job processing failed; durable state requires recovery")
                del self._active[job_id]

    async def run_once(self) -> int:
        self._reap()
        capacity = self._max_concurrent - len(self._active)
        if capacity <= 0:
            return 0
        processed = 0
        for _ in range(capacity):
            claimed = await ml_job_repo.claim_next(self._holder, lease_seconds=self._lease_seconds)
            if claimed is None:
                break
            processed += 1
            if claimed.action == "skip":
                continue
            self._active[f"{claimed.job_id}:{uuid4()}"] = asyncio.create_task(
                self.process_claimed(claimed), name=f"nova-ml-job-{claimed.job_id}"
            )
        return processed

    async def _session(self, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """The caller's session, or refuse: never a substitute identity."""
        session_id = await job_session_id(self._client, str(job["id"]))
        session = await session_store.get(session_id) if session_id else None
        if (
            not session_id
            or session_fingerprint(session_id) != job["session_fingerprint"]
            or not session
            or not session.get("encrypted_password")
            or session.get("username") != job["actor"]
            or session.get("active_role") != job["active_role"]
            or int(session.get("security_context_version") or 1)
            != int(job["security_context_version"])
        ):
            raise MLJobSessionExpired("The session that requested this training has ended")
        try:
            SecurityContext.from_session({**session, "session_id": session_id})
        except (SecurityContextError, ValueError, TypeError):
            raise MLJobSessionExpired("The training session has no valid active role") from None
        return session_id, session

    async def _heartbeat(
        self,
        claimed: ClaimedJob,
        session_id: str,
        training: asyncio.Task,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_interval)
            if stop.is_set():
                return
            try:
                held, cancelled = await ml_job_repo.heartbeat(
                    claimed,
                    lease_seconds=self._lease_seconds,
                )
                if not held or cancelled:
                    training.cancel()
                    return
                # A role change or logout cancels an in-flight run too.
                await self._session(claimed.job)
                await session_store.refresh(session_id)
            except Exception as exc:
                logger.error("ML job heartbeat failed: %s", type(exc).__name__)
                training.cancel()
                return

    async def process_claimed(self, claimed: ClaimedJob) -> None:
        from app.modules.ml_engine.service import ml_engine_service

        if claimed.action in {"verify", "complete"}:
            status = await ml_job_repo.settle(claimed, None)
            if status in {"succeeded", "failed", "cancelled"}:
                await clear_job_session(self._client, claimed.job_id)
            return
        if claimed.action != "train":
            return
        job = claimed.job
        stop_heartbeat = asyncio.Event()
        heartbeat: asyncio.Task[None] | None = None
        training: asyncio.Task | None = None
        stopping = False
        outcome = LocalOutcome("failed", error_kind="failed", error_message="Training failed")
        try:
            session_id, session = await self._session(job)
            request = json.loads(job["request_json"])
            if not isinstance(request, dict) or claimed.operation_id is None:
                raise ValueError("Invalid durable training definition")
            with acting_as_sync(
                SecurityContext.from_session({**session, "session_id": session_id})
            ):
                training = asyncio.create_task(
                    ml_engine_service.train_model_locally(
                        training_sql=training_sql(job),
                        username=job["actor"],
                        password=decrypt_password(session["encrypted_password"]),
                        role=job["active_role"],
                        security_context_version=int(job["security_context_version"]),
                        tenant=job.get("tenant") or "default",
                        created_by=job["actor"],
                        reservation_token=str(claimed.operation_id),
                        **request,
                    )
                )
            heartbeat = asyncio.create_task(
                self._heartbeat(claimed, session_id, training, stop_heartbeat)
            )
            outcome = LocalOutcome("succeeded", result=await training)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            stopping = current is not None and current.cancelling() > 0
            outcome = LocalOutcome(
                "interrupted" if stopping else "cancelled",
                error_kind="cancelled",
                error_message="Training was interrupted" if stopping else "Training was cancelled",
            )
        except MLJobSessionExpired:
            outcome = LocalOutcome(
                "failed",
                error_kind="session_expired",
                error_message="The training session has ended",
            )
        except ValueError:
            outcome = LocalOutcome(
                "failed", error_kind="invalid_request", error_message="Invalid training request"
            )
        except Exception as exc:
            logger.error("ML training failed: %s", type(exc).__name__)
        finally:
            stop_heartbeat.set()
            if training is not None and not training.done():
                training.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await training
            if heartbeat is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
        status = await ml_job_repo.settle(claimed, outcome)
        if status in {"succeeded", "failed", "cancelled"}:
            await clear_job_session(self._client, claimed.job_id)
            with contextlib.suppress(Exception):
                await write_audit_log(
                    event_type="ml",
                    user_name=job["actor"],
                    action="train",
                    object_type="ml_job",
                    object_name=claimed.job_id,
                    status="SUCCESS" if status == "succeeded" else "ERROR",
                    error_message=outcome.error_message,
                    active_role=job["active_role"],
                    security_context_version=int(job["security_context_version"]),
                )
        if stopping:
            raise asyncio.CancelledError()
