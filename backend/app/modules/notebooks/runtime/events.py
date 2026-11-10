"""Notebook events: a bounded Redis stream per notebook, served as SSE.

The worker publishes; every open tab of the notebook reads. A tab that
reconnects passes the last event id it saw and gets everything after it, so a
refresh or a dropped connection never loses output and never re-runs a cell.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.modules.notebooks.runtime import state

logger = logging.getLogger(__name__)

#: Events kept per notebook; older ones are trimmed (reload reads durable state).
MAX_EVENTS = 5000
HEARTBEAT_SECONDS = 10
#: A stream ends after this long and the tab reconnects from its last event.
#: Short-lived streams never hold a server shutdown or reload hostage.
STREAM_SECONDS = 25
_EVENT_ID = r"^\d{1,20}-\d{1,10}$"


def frame(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


async def publish(notebook_id: str, event: str, data: dict[str, Any]) -> str:
    client = state.redis()
    key = state.events_key(notebook_id)
    event_id = await client.xadd(
        key,
        {"event": event, "data": json.dumps(data, separators=(",", ":"), default=str)},
        maxlen=MAX_EVENTS,
        approximate=True,
    )
    await client.expire(key, state.DAY)
    return event_id


def _valid_cursor(cursor: str | None) -> str:
    import re

    if cursor and re.match(_EVENT_ID, cursor):
        return cursor
    return "$"


async def open_stream_client() -> tuple[Any, bool]:
    """A Redis client for one long-lived event stream, and whether to close it.

    A blocking read holds its connection for the whole block, so a stream gets
    its own client (with a read timeout longer than the block) instead of
    taking a connection from the shared pool for as long as a tab stays open.
    """
    import redis.asyncio as aioredis

    from app.core.config import settings

    client = aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_timeout=HEARTBEAT_SECONDS + 15,
        socket_connect_timeout=5,
    )
    return client, True


async def stream(
    notebook_id: str,
    *,
    last_event_id: str | None,
    snapshot: dict[str, Any],
    is_disconnected,
) -> AsyncIterator[str]:
    """Yield SSE frames: a status snapshot, then events after ``last_event_id``."""
    client, owned = await open_stream_client()
    try:
        async for frame_text in _stream(client, notebook_id, last_event_id, snapshot,
                                        is_disconnected):
            yield frame_text
    finally:
        if owned:
            await client.aclose()


async def _stream(
    client: Any,
    notebook_id: str,
    last_event_id: str | None,
    snapshot: dict[str, Any],
    is_disconnected,
) -> AsyncIterator[str]:
    key = state.events_key(notebook_id)
    cursor = _valid_cursor(last_event_id)
    if cursor == "$":
        # Start after the newest event that exists now; the snapshot covers
        # everything before it.
        latest = await client.xrevrange(key, count=1)
        cursor = latest[0][0] if latest else "0-0"
    yield frame("snapshot", snapshot)
    ends_at = asyncio.get_running_loop().time() + STREAM_SECONDS
    while asyncio.get_running_loop().time() < ends_at:
        if await is_disconnected():
            return
        try:
            remaining = max(1.0, ends_at - asyncio.get_running_loop().time())
            block = int(min(HEARTBEAT_SECONDS, remaining) * 1000)
            batch = await client.xread({key: cursor}, count=200, block=block)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a transient Redis error ends this stream
            logger.warning("Notebook event stream read failed", exc_info=True)
            return
        if not batch:
            yield ": keep-alive\n\n"
            continue
        for _stream, entries in batch:
            for event_id, fields in entries:
                cursor = event_id
                try:
                    data = json.loads(fields.get("data") or "{}")
                except ValueError:
                    continue
                yield frame(fields.get("event") or "message", data, event_id)
