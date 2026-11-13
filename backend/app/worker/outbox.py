"""Publish committed job wakeups; PostgreSQL remains the work authority."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any
from uuid import uuid4

from redis.exceptions import RedisError

from app.common.durable_jobs import OutboxRepository
from app.common.job_wakeups import publish_wakeup
from app.common.metadata_actors import service_actor
from app.core.metadata_actor import metadata_transaction
from app.core.metadata_db import MetadataConflict, MetadataReadOnlyMaintenance

logger = logging.getLogger(__name__)
POLL_SECONDS = 1.0
PUBLISH_SECONDS = 2.0
LEASE_SECONDS = 60
BATCH_SIZE = 16


class OutboxPublisher:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.holder = uuid4()
        self.actor = service_actor("outbox")

    async def publish_batch(self, stop: asyncio.Event) -> int:
        async with metadata_transaction(self.actor) as (connection, _):
            events = await OutboxRepository(connection).claim(
                self.holder,
                seconds=LEASE_SECONDS,
                limit=BATCH_SIZE,
            )
        completed = 0
        for event in events:
            if stop.is_set():
                break
            try:
                async with asyncio.timeout(PUBLISH_SECONDS):
                    await publish_wakeup(self.client, OutboxRepository.notification(event))
            except (RedisError, OSError, TimeoutError):
                # A timeout may have appended already. Repeating this ID is safe:
                # consumers claim the durable job, never an effect from the stream.
                async with metadata_transaction(self.actor) as (connection, _):
                    try:
                        await OutboxRepository(connection).defer(
                            event,
                            delay_seconds=min(30, 2 ** min(event["publish_attempt"], 5)),
                        )
                    except MetadataConflict:
                        logger.debug("Outbox deferral fenced by a newer claim")
                continue
            async with metadata_transaction(self.actor) as (connection, _):
                try:
                    await OutboxRepository(connection).delivered(event)
                except MetadataConflict:
                    logger.debug("Outbox acknowledgment fenced by a newer claim")
                    continue
            completed += 1
        return completed

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                completed = await self.publish_batch(stop_event)
            except MetadataReadOnlyMaintenance:
                completed = 0
            if completed == BATCH_SIZE:
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
