"""The long-running scheduler loop: leader-gated ticks over an interval.

The scheduler is a singleton guarded by a Redis leader lock. Only the lock
holder runs ticks; a non-holder stays idle and retries acquisition, so two
instances never enqueue the same due-time. Losing the lock mid-run stops the
loop from firing without crashing the process — it simply waits to win again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.core.config import settings
from app.modules.task_orchestration.repository import task_orchestration_repository
from app.modules.task_orchestration.scheduler import SchedulerTick
from app.modules.task_orchestration.transport import GraphRunTransport, LeaderLock

logger = logging.getLogger(__name__)


class SchedulerService:
    """Runs ticks on a cadence while this instance holds the leader lock."""

    def __init__(
        self,
        tick: SchedulerTick,
        leader_lock: LeaderLock,
        poll_interval: float | None = None,
    ) -> None:
        self._tick = tick
        self._leader_lock = leader_lock
        self._poll_interval = (
            poll_interval if poll_interval is not None else settings.SCHEDULER_POLL_INTERVAL_SECONDS
        )

    async def run_once(self) -> bool:
        """Acquire-if-possible then tick. Returns True when the tick ran.

        The lock is not held across the sleep: another instance may take over
        the moment this one stops ticking, and the deterministic run id makes
        the handover safe even if both believed they were leader.
        """
        if not self._leader_lock.is_leader and not await self._leader_lock.acquire():
            return False
        if not await self._leader_lock.renew():
            logger.info("scheduler leader lock lost; standing by")
            return False

        await self._tick.tick()
        return True

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        logger.info("nova-scheduler started (poll=%.1fs)", self._poll_interval)
        while not stop_event.is_set():
            try:
                ran = await self.run_once()
                if not ran:
                    logger.debug("not leader; skipping tick")
            except Exception:
                logger.exception("scheduler tick failed; continuing")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)

        await self._leader_lock.release()
        logger.info("nova-scheduler stopped")


def build_scheduler_service(
    transport: GraphRunTransport,
    leader_lock: LeaderLock,
) -> SchedulerService:
    """Wire the default service from a transport and a leader lock."""
    tick = SchedulerTick(task_orchestration_repository, transport)
    return SchedulerService(tick, leader_lock)
