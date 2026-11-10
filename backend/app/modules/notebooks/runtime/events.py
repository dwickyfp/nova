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
HEARTBEAT_SECONDS = 15
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


async def stream(
    notebook_id: str,
    *,
    last_event_id: str | None,
    snapshot: dict[str, Any],
    is_disconnected,
) -> AsyncIterator[str]:
    """Yield SSE frames: a status snapshot, then events after ``last_event_id``."""
    client = state.redis()
    key = state.events_key(notebook_id)
    cursor = _valid_cursor(last_event_id)
    if cursor == "$":
        # Start after the newest event that exists now; the snapshot covers
        # everything before it.
        latest = await client.xrevrange(key, count=1)
        cursor = latest[0][0] if latest else "0-0"
    yield frame("snapshot", snapshot)
    while True:
        if await is_disconnected():
            return
        try:
            batch = await client.xread({key: cursor}, count=200, block=HEARTBEAT_SECONDS * 1000)
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
