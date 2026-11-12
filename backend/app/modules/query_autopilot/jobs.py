"""Durable operation intents and leases, consumed by the existing nova-worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from app.modules.query_autopilot.models import digest, utcnow
from app.modules.query_autopilot.repository import (
    SCHEDULER_ACTOR,
    WORKER_ACTOR,
    acting,
    repository,
)
from app.modules.query_autopilot.runtime import AuthorizationUnavailable
from app.modules.task_orchestration.transport import LeaderLock


class LeaseLost(ValueError):
    pass


@asynccontextmanager
async def exclusive(client, keys: tuple[str, ...], *, renew_interval: float = 10):
    locks: list[LeaderLock] = []
    task = asyncio.current_task()
    lost = False

    async def renew():
        nonlocal lost
        while True:
            await asyncio.sleep(renew_interval)
            try:
                held = all([await lock.renew() for lock in locks])
            except Exception:
                held = False
            if not held:
                lost = True
                if task:
                    task.cancel()
                return

    renewal = None
    try:
        for key in sorted(set(keys)):
            lock = LeaderLock(client, key="nova:autopilot:" + digest(key), ttl_seconds=30)
            if not await lock.acquire():
                raise ValueError("operation_in_progress")
            locks.append(lock)
        renewal = asyncio.create_task(renew())
        yield
    except asyncio.CancelledError:
        if lost:
            if task:
                task.uncancel()
            raise LeaseLost("coordination_lease_lost_outcome_uncertain") from None
        raise
    finally:
        if renewal:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
        for lock in reversed(locks):
            await lock.release()


async def schedule_due_jobs() -> None:
    now = utcnow()
    with acting(SCHEDULER_ACTOR):
        for kind, seconds in (("aggregate", 60), ("cleanup", 3600)):
            bucket = int(now.timestamp()) // seconds
            identifier = f"{kind}:{bucket}"
            await repository.put(
                "jobs",
                identifier,
                {
                    "id": identifier,
                    "kind": kind,
                    "version": 1,
                    "state": "QUEUED",
                    "created_at": now.isoformat(),
                },
                state="QUEUED",
                insert_only=True,
            )


# A run whose lease has not been renewed for this long is retaken for
# reconciliation; the holder renews it every HEARTBEAT_SECONDS.
LEASE_STALE_SECONDS = 300
HEARTBEAT_SECONDS = 10

AUTHORIZATION_REASONS = {
    "ranger_policy_revision_unavailable", "ranger_policy_revision_changed",
    "explicit_active_role_required", "session_expired",
    "security_context_changed", "delegated_credential_unavailable",
    "delegated_identity_unavailable", "engine_authorization_refused",
    "explicit_execution_consent_required",
}


@asynccontextmanager
async def leased(repo, job_id: str, generation: int, *, interval: float = HEARTBEAT_SECONDS):
    """Hold a claimed job lease: renew it, and interrupt the run when it is lost.

    A heartbeat refused by the store is a lost lease. A store that stays
    unreachable for half the stale period is treated the same way, before
    another worker may retake the job.
    """
    task = asyncio.current_task()
    lost = False

    async def beat():
        nonlocal lost
        renewed = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            try:
                with acting(WORKER_ACTOR):
                    held = await repo.heartbeat_job(job_id, generation=generation)
                if held:
                    renewed = time.monotonic()
            except Exception:
                held = time.monotonic() - renewed < LEASE_STALE_SECONDS / 2
            if not held:
                lost = True
                if task:
                    task.cancel()
                return

    renewal = asyncio.create_task(beat())
    try:
        with acting(WORKER_ACTOR, lease=(job_id, generation)):
            yield
    except asyncio.CancelledError:
        if lost:
            if task:
                task.uncancel()
            raise LeaseLost("job_lease_lost_outcome_uncertain") from None
        raise
    finally:
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal


class AutopilotWorker:
    def __init__(self, client, service, repo=repository):
        self.client, self.service, self.repo = client, service, repo
        self.cursors = {"RUNNING": "", "QUEUED": ""}

    async def next_page(self, state: str) -> list[dict]:
        page = await self.repo.page("jobs", state=state, after=self.cursors[state], limit=100)
        if not page and self.cursors[state]:
            page = await self.repo.page("jobs", state=state, limit=100)
        self.cursors[state] = page[-1]["id"] if page else ""
        return page

    async def run_once(self) -> None:
        with acting(WORKER_ACTOR):
            await self._run_once()

    async def _run_once(self) -> None:
        # Interrupted runs need reconciliation, never another submission. The
        # retake advances the lease, so the interrupted holder is fenced out.
        for item in await self.next_page("RUNNING"):
            try:
                async with exclusive(self.client, ("job:" + item["id"],)):
                    generation = await self.repo.retake_job(
                        item["id"], older_than_seconds=LEASE_STALE_SECONDS
                    )
                    if generation is None:
                        continue
                    async with leased(self.repo, item["id"], generation):
                        fresh = await self.repo.get("jobs", item["id"])
                        if fresh and fresh["state"] == "RUNNING":
                            await self.service.reconcile(fresh)
            except ValueError:
                continue
        for item in await self.next_page("QUEUED"):
            if item.get("not_before") and datetime.fromisoformat(item["not_before"]) > utcnow():
                continue
            try:
                async with exclusive(self.client, ("job:" + item["id"],)):
                    fresh = await self.repo.get("jobs", item["id"])
                    if not fresh or fresh["state"] != "QUEUED":
                        continue
                    generation = await self.repo.claim_job(
                        item["id"], expected_version=fresh["version"]
                    )
                    if generation is None:
                        continue
                    running = {**fresh, "state": "RUNNING", "version": fresh["version"] + 1}
                    async with leased(self.repo, item["id"], generation):
                        result = {}
                        try:
                            result = await self.service.run_operation(running)
                            status, reason = result.get("state", "COMPLETED"), result.get("reason")
                        except AuthorizationUnavailable as exc:
                            status = "BLOCKED"
                            reason = (
                                str(exc) if str(exc) in AUTHORIZATION_REASONS
                                else "authorization_unavailable"
                            )
                        except LeaseLost:
                            raise
                        except Exception:
                            status, reason = "BLOCKED", "operation_failed_or_outcome_unknown"
                        done = {
                            **running,
                            "state": status,
                            "reason": reason,
                            "version": running["version"] + 1,
                            "finished_at": utcnow().isoformat(),
                            "not_before": result.get("not_before"),
                        }
                        if not await self.repo.put(
                            "jobs",
                            item["id"],
                            done,
                            state=status,
                            version=done["version"],
                            expected_version=running["version"],
                        ):
                            logging.getLogger(__name__).warning(
                                "Autopilot job lease lost before its result was recorded"
                            )
            except ValueError:
                continue

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logging.getLogger(__name__).warning("Autopilot worker metadata unavailable")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2)
