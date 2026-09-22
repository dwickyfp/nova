"""Redis Streams consumer for ``nova-worker``.

The worker reads graph-run jobs from the scheduler's stream through a consumer
group, so N workers share the load and an unacknowledged job is redelivered.
The stream is **transport only**: the payload carries ids, the graph is reloaded
from ``NOVA_SYSTEM``, and a redelivery is idempotent by construction (see
:mod:`worker`).

No credential ever appears in the stream, and none is written back.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


class GraphRunConsumer:
    """A consumer-group reader over the graph-run stream."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        group: str | None = None,
        consumer: str | None = None,
        stream_key: str | None = None,
    ) -> None:
        self._client = client
        self._group = group if group is not None else settings.TASK_STREAM_GROUP
        self._consumer = consumer if consumer is not None else settings.WORKER_NAME
        self._stream_key = stream_key if stream_key is not None else settings.TASK_STREAM_KEY

    @property
    def stream_key(self) -> str:
        return self._stream_key

    async def ensure_group(self) -> None:
        """Create the consumer group at the stream head if it does not exist.

        ``mkstream=True`` lets the worker start before the scheduler has ever
        published; ``id="$"`` means "new messages only", so a fresh worker does
        not replay ancient jobs — the reconciler owns catch-up.
        """
        try:
            await self._client.xgroup_create(self._stream_key, self._group, id="$", mkstream=True)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read(
        self, *, count: int | None = None, block_ms: int = 5000
    ) -> list[tuple[str, dict[str, str]]]:
        """Read up to ``count`` new jobs. Returns ``(stream_id, payload)`` pairs."""
        batch = count if count is not None else settings.WORKER_STREAM_BATCH_SIZE
        response = await self._client.xreadgroup(
            self._group,
            self._consumer,
            {self._stream_key: ">"},
            count=batch,
            block=block_ms,
        )
        if not response:
            return []
        jobs: list[tuple[str, dict[str, str]]] = []
        entry_streams = cast("list[Any]", response)
        for _stream, entries in entry_streams:
            for stream_id, fields in entries:
                jobs.append((str(stream_id), dict(fields)))
        return jobs

    async def ack(self, stream_id: str) -> None:
        await self._client.xack(self._stream_key, self._group, stream_id)

    async def claim_stale(
        self, *, min_idle_ms: int = 60000, count: int = 10
    ) -> list[tuple[str, dict[str, str]]]:
        """Claim jobs whose consumer died before acking.

        A worker that crashed mid-job leaves its delivery pending; another
        worker re-owns it after ``min_idle_ms`` and the idempotent handler
        finishes the work.
        """
        response = await self._client.xautoclaim(
            self._stream_key,
            self._group,
            self._consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        # redis-py returns (next_id, entries, deleted) on 6.2+, (next_id, entries)
        # on older servers.
        payload = cast("list[Any]", response)
        entries = cast("list[Any]", payload[1]) if len(payload) > 1 else []
        return [(str(sid), dict(fields)) for sid, fields in entries]


def job_fields_are_safe(fields: dict[str, Any]) -> bool:
    """True when a payload carries no credential-shaped key or value."""
    serialized = str(fields).lower()
    return not any(bad in serialized for bad in ("password", "secret", "token", "credential"))
