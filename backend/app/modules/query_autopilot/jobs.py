"""Durable operation intents and leases, consumed by the existing nova-worker."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from app.modules.query_autopilot.models import digest, utcnow
from app.modules.query_autopilot.repository import repository
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
        # Interrupted applications need reconciliation, never another submission.
        stale = await self.next_page("RUNNING")
        for item in stale:
            if datetime.fromisoformat(item["started_at"]) > utcnow() - timedelta(minutes=20):
                continue
            try:
                async with exclusive(self.client, ("job:" + item["id"],)):
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
                    running = {
                        **fresh,
                        "state": "RUNNING",
                        "version": fresh["version"] + 1,
                        "started_at": utcnow().isoformat(),
                    }
                    if not await self.repo.put(
                        "jobs",
                        item["id"],
                        running,
                        state="RUNNING",
                        version=running["version"],
                        expected_version=fresh["version"],
                    ):
                        continue
                    result = {}
                    try:
                        result = await self.service.run_operation(running)
                        status, reason = result.get("state", "COMPLETED"), result.get("reason")
                    except AuthorizationUnavailable as exc:
                        status = "BLOCKED"
                        reason = str(exc) if str(exc) in {
                            "ranger_policy_revision_unavailable", "ranger_policy_revision_changed",
                            "explicit_active_role_required", "session_expired",
                            "security_context_changed", "delegated_credential_unavailable",
                            "delegated_identity_unavailable", "engine_authorization_refused",
                            "explicit_execution_consent_required",
                        } else "authorization_unavailable"
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
                    await self.repo.put(
                        "jobs",
                        item["id"],
                        done,
                        state=status,
                        version=done["version"],
                        expected_version=running["version"],
                    )
            except ValueError:
                continue

    async def run_forever(self, stop: asyncio.Event) -> None:
        import logging

        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logging.getLogger(__name__).warning("Autopilot worker metadata unavailable")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2)
