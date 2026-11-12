"""Redis wakeups for durable jobs: transport only, never authority.

The outbox publisher appends ``{event_id, job_id}`` to one capped stream after
the job and its outbox event committed in PostgreSQL. Workers wait on the
stream only to shorten their poll interval: they always claim from the PG
queue, so a lost, duplicated or flushed wakeup changes latency, not outcome.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

WAKEUP_STREAM = "nova:jobs:wakeup"
WAKEUP_STREAM_MAXLEN = 10_000


async def publish_wakeup(client: Any, notification: dict[str, str]) -> None:
    """Append one wakeup; raises when Redis refuses so the publisher defers."""
    await client.xadd(
        WAKEUP_STREAM, notification, maxlen=WAKEUP_STREAM_MAXLEN, approximate=True,
    )


class WakeupListener:
    """Waits for the next wakeup after the last one seen, or a timeout."""

    def __init__(self, client: Any | None) -> None:
        self._client = client
        self._last_id = "$"

    async def wait(self, timeout_seconds: float) -> bool:
        """True when a wakeup arrived; False on timeout or Redis failure."""
        if self._client is None:
            await asyncio.sleep(timeout_seconds)
            return False
        try:
            response = await self._client.xread(
                {WAKEUP_STREAM: self._last_id},
                count=100,
                block=max(1, int(timeout_seconds * 1000)),
            )
        except Exception:  # noqa: BLE001 - Redis is a latency aid only
            logger.debug("job wakeup stream unavailable; polling PostgreSQL")
            await asyncio.sleep(timeout_seconds)
            return False
        if not response:
            return False
        for _stream, entries in response:
            if entries:
                self._last_id = str(entries[-1][0])
        return True
