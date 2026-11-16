"""Who else has a dashboard open, and where their pointer is.

Presence is ephemeral collaboration state, so it lives in Redis with a short
expiry and never in the document store. An entry names the person (their Nova
user name) and their position on the canvas; it never holds a session id,
role or data. Losing every entry only hides other people until their next
heartbeat; committed edits are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.metadata_scope import actor_for

#: A viewer who has not sent a heartbeat for this long has left.
STALE_SECONDS = 15
#: The read permission check is reused this long, keyed by security-context version.
ACCESS_SECONDS = 30
#: Most people listed per dashboard; the rest are counted.
MAX_LISTED = 20
KEY = "nova:viz:presence:{dashboard}"
ACCESS = "nova:viz:presence-access:{dashboard}:{viewer}:{version}"

_client: aioredis.Redis | None = None


def _now() -> float:
    return time.time()


def client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def viewer_key(session_id: str) -> str:
    """A stable, non-reversible id for one browser session on one dashboard."""
    return hashlib.sha256(f"nova-viz-presence:{session_id}".encode()).hexdigest()[:24]


def _cursor(value: Mapping[str, Any] | None) -> dict[str, float] | None:
    if value is None:
        return None
    x, y = value.get("x"), value.get("y")
    if not isinstance(x, int | float) or not isinstance(y, int | float):
        return None
    if isinstance(x, bool) or isinstance(y, bool) or not (math.isfinite(x) and math.isfinite(y)):
        return None
    return {"x": round(min(max(float(x), 0.0), 1.0), 4),
            "y": round(min(max(float(y), 0.0), 100_000.0), 1)}


class PresenceService:
    def __init__(self, redis: Callable[[], aioredis.Redis] = client) -> None:
        self._redis = redis

    async def _check_access(self, user: Mapping[str, Any], dashboard: UUID,
                            can_view: Callable[[], Awaitable[bool]]) -> str:
        actor = actor_for(user)
        if not actor.session_id:
            raise PermissionError("No session")
        viewer = viewer_key(actor.session_id)
        redis = self._redis()
        cached = ACCESS.format(dashboard=dashboard, viewer=viewer,
                               version=actor.security_context_version)
        if not await redis.get(cached):
            if not await can_view():
                raise PermissionError("No access")
            await redis.set(cached, "1", ex=ACCESS_SECONDS)
        return viewer

    async def beat(self, user: Mapping[str, Any], dashboard: UUID, *, page_id: UUID | None,
                   cursor: Mapping[str, Any] | None, editing: bool,
                   can_view: Callable[[], Awaitable[bool]]) -> dict[str, Any]:
        """Record this viewer and return everyone else on the dashboard."""
        viewer = await self._check_access(user, dashboard, can_view)
        actor = actor_for(user)
        now = _now()
        key = KEY.format(dashboard=dashboard)
        redis = self._redis()
        entry = {"name": actor.principal, "page_id": str(page_id) if page_id else None,
                 "cursor": _cursor(cursor), "editing": bool(editing), "at": now}
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, viewer, json.dumps(entry, separators=(",", ":")))
            pipe.expire(key, STALE_SECONDS * 2)
            pipe.hgetall(key)
            *_, stored = await pipe.execute()
        others: list[dict[str, Any]] = []
        stale: list[str] = []
        for field, raw in stored.items():
            if field == viewer:
                continue
            try:
                item = json.loads(raw)
                at = float(item["at"])
            except (TypeError, ValueError, KeyError):
                stale.append(field)
                continue
            if now - at > STALE_SECONDS:
                stale.append(field)
                continue
            others.append({"viewer": field, "name": str(item.get("name") or ""),
                           "page_id": item.get("page_id"), "cursor": _cursor(item.get("cursor")),
                           "editing": bool(item.get("editing")), "seconds_ago": round(now - at, 1)})
        if stale:
            await redis.hdel(key, *stale)
        others.sort(key=lambda item: (item["name"].casefold(), item["viewer"]))
        return {"viewer": viewer, "others": others[:MAX_LISTED],
                "more": max(0, len(others) - MAX_LISTED)}

    async def leave(self, user: Mapping[str, Any], dashboard: UUID) -> None:
        actor = actor_for(user)
        if actor.session_id:
            await self._redis().hdel(KEY.format(dashboard=dashboard), viewer_key(actor.session_id))


native_presence = PresenceService()
