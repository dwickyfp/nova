"""Scheduler leadership held in PostgreSQL, fenced by an increasing token.

Replaces the Redis leader key for the Task scheduler: a lease names its holder
and token, renewal requires both, and every occurrence reservation locks the
live lease row in the same transaction. A leader whose lease expired cannot
publish, even if it raced a successor.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from app.common.metadata_actors import metadata_scope, service_actor
from app.core.config import settings
from app.core.metadata_db import MetadataConflict
from app.modules.task_orchestration.metadata_scheduler import SchedulerLease, SchedulerRepository

logger = logging.getLogger(__name__)

SCHEDULER_LEASE_NAME = "task.scheduler"
_MAX_LEASE_SECONDS = 300


class MetadataLeaderLock:
    """The scheduler's leader lease, with the interface the scheduler loop uses."""

    def __init__(self, name: str = SCHEDULER_LEASE_NAME, ttl_seconds: int | None = None) -> None:
        configured = ttl_seconds or int(settings.SCHEDULER_LEADER_LOCK_TTL_SECONDS)
        self._name = name
        self._ttl = max(1, min(_MAX_LEASE_SECONDS, configured))
        self._holder: UUID = uuid4()
        self._lease: SchedulerLease | None = None
        self._actor = service_actor("scheduler")

    @property
    def is_leader(self) -> bool:
        return self._lease is not None

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @property
    def lease(self) -> SchedulerLease | None:
        return self._lease

    async def acquire(self) -> bool:
        async with metadata_scope(self._actor) as connection:
            lease = await SchedulerRepository(connection).acquire(
                self._name, self._holder, seconds=self._ttl,
            )
        self._lease = lease
        return lease is not None

    async def renew(self) -> bool:
        """Extend the lease. Returns False (and drops leader state) if lost."""
        lease = self._lease
        if lease is None:
            return False
        try:
            async with metadata_scope(self._actor) as connection:
                await SchedulerRepository(connection).renew(lease, seconds=self._ttl)
        except MetadataConflict:
            self._lease = None
            return False
        return True

    async def release(self) -> None:
        lease, self._lease = self._lease, None
        if lease is None:
            return
        try:
            async with metadata_scope(self._actor) as connection:
                await SchedulerRepository(connection).release(lease)
        except MetadataConflict:
            logger.info("scheduler lease had already expired at release")
