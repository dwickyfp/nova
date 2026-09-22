"""Redis Streams transport and leader election for the scheduler.

Redis is **ephemeral transport only** — the scheduler persists the graph run to
``NOVA_SYSTEM`` *before* pushing a job here, so a Redis flush loses no work
(design §2, rule 1). The leader lock makes the scheduler a singleton without a
separate coordination service.

The job payload carries ids and metadata only — never a credential. See
``docs/specs/nova-23-task-orchestration-design.md`` §5.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Release the lock only if we still hold it: DEL alone could delete a lock that
# a different instance acquired after our TTL expired.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# Extend the lock only if we still hold it, for the same reason.
_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""


class GraphRunTransport(Protocol):
    """The transport surface the scheduler depends on.

    The scheduler talks to this, not to Redis directly, so a fake can stand in
    for it in tests and the ordering guarantee (persist, then push) is separable
    from the Redis client.
    """

    async def publish_graph_run(self, graph_run: dict[str, Any], task_ids: list[str]) -> str:
        """Push a persisted graph run to the worker stream. Returns the stream id."""
        ...


class RedisGraphRunTransport:
    """``XADD``-based transport over an existing single Redis instance."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def publish_graph_run(self, graph_run: dict[str, Any], task_ids: list[str]) -> str:
        payload: dict[str, str] = {
            "graph_run_id": str(graph_run["id"]),
            "graph_id": str(graph_run["graph_id"]),
            "trigger_type": str(graph_run.get("trigger_type", "schedule")),
            "task_ids": ",".join(task_ids),
        }
        stream_id = await self._client.xadd(
            settings.TASK_STREAM_KEY,
            payload,  # type: ignore[arg-type]
            maxlen=settings.TASK_STREAM_MAXLEN,
            approximate=True,
        )
        return str(stream_id)


class LeaderLock:
    """A Redis-backed single-writer lock with an ownership token.

    Acquire is ``SET key token NX EX ttl``; release and renew are compare-and-set
    Lua scripts so a holder whose TTL lapsed cannot delete or extend a successor's
    lock. Losing the lock is not fatal — the scheduler simply stops firing until
    it wins again — which is what keeps two instances from double-enqueueing.
    """

    def __init__(
        self,
        client: aioredis.Redis,
        key: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._client = client
        self._key = key if key is not None else settings.SCHEDULER_LEADER_LOCK_KEY
        self._ttl = (
            ttl_seconds if ttl_seconds is not None else settings.SCHEDULER_LEADER_LOCK_TTL_SECONDS
        )
        self._token: str | None = None

    @property
    def is_leader(self) -> bool:
        return self._token is not None

    async def acquire(self) -> bool:
        token = str(uuid.uuid4())
        acquired = await self._client.set(self._key, token, nx=True, ex=self._ttl)
        if acquired:
            self._token = token
            return True
        return False

    async def renew(self) -> bool:
        """Extend the lock. Returns False (and drops leader state) if lost."""
        if self._token is None:
            return False
        renewed = await self._client.eval(_RENEW_SCRIPT, 1, self._key, self._token, self._ttl)
        if not renewed:
            self._token = None
            return False
        return True

    async def release(self) -> None:
        if self._token is None:
            return
        await self._client.eval(_RELEASE_SCRIPT, 1, self._key, self._token)
        self._token = None
