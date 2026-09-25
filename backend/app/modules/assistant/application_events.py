"""Owner-scoped, short-lived feedback for actions dispatched to Nova surfaces."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from app.modules.assistant.app_context import NoveApplicationEvent

_MAX_THREADS = 1024
_MAX_PENDING = 32
_MAX_EVENTS = 12
_TTL_SECONDS = 600


@dataclass
class _ThreadFeedback:
    user_name: str
    updated_at: float
    pending: dict[str, tuple[str, str]] = field(default_factory=dict)
    events: list[NoveApplicationEvent] = field(default_factory=list)


class ApplicationEventBroker:
    """Keep UI action outcomes available until the next assistant turn."""

    def __init__(self) -> None:
        self._threads: dict[str, _ThreadFeedback] = {}
        self._lock = threading.Lock()

    def _entry(self, thread_id: str, user_name: str) -> _ThreadFeedback:
        now = time.monotonic()
        for key, item in list(self._threads.items()):
            if now - item.updated_at > _TTL_SECONDS:
                del self._threads[key]
        entry = self._threads.get(thread_id)
        if entry is None or entry.user_name != user_name:
            entry = _ThreadFeedback(user_name=user_name, updated_at=now)
            self._threads[thread_id] = entry
        entry.updated_at = now
        if len(self._threads) > _MAX_THREADS:
            oldest = min(self._threads, key=lambda key: self._threads[key].updated_at)
            if oldest != thread_id:
                del self._threads[oldest]
        return entry

    def register_action(self, thread_id: str, user_name: str, action: dict[str, Any]) -> None:
        correlation = action.get("correlation_id")
        surface = action.get("surface_id")
        capability = action.get("capability")
        if not all(isinstance(item, str) and item for item in (correlation, surface, capability)):
            return
        with self._lock:
            existing = self._threads.get(thread_id)
            if existing is not None and existing.user_name != user_name:
                return
            entry = self._entry(thread_id, user_name)
            entry.pending[correlation] = (surface, capability)
            if len(entry.pending) > _MAX_PENDING:
                del entry.pending[next(iter(entry.pending))]

    def publish(
        self, thread_id: str, user_name: str, event: NoveApplicationEvent
    ) -> Literal["verified", "failed", "unmatched"]:
        verification: Literal["verified", "failed", "unmatched"] = "unmatched"
        with self._lock:
            existing = self._threads.get(thread_id)
            if existing is not None and existing.user_name != user_name:
                return "unmatched"
            entry = self._entry(thread_id, user_name)
            if event.id in {item.id for item in entry.events}:
                return "unmatched"
            if event.type in {"ui_action_completed", "ui_action_failed"}:
                expected = entry.pending.get(event.correlation_id or "")
                if (
                    expected is not None
                    and event.source == "assistant"
                    and event.surface_id == expected[0]
                    and event.payload.get("capability") == expected[1]
                    and event.status == (
                        "success" if event.type == "ui_action_completed" else "failure"
                    )
                ):
                    verification = (
                        "verified" if event.type == "ui_action_completed" else "failed"
                    )
                    del entry.pending[event.correlation_id or ""]
                else:
                    return "unmatched"
            entry.events.append(event)
            entry.events = entry.events[-_MAX_EVENTS:]
        return verification

    def recent_events(
        self, thread_id: str, user_name: str, surface_id: str
    ) -> list[NoveApplicationEvent]:
        with self._lock:
            entry = self._threads.get(thread_id)
            if entry is None or entry.user_name != user_name:
                return []
            if time.monotonic() - entry.updated_at > _TTL_SECONDS:
                return []
            return [event for event in entry.events if event.surface_id == surface_id]

    def remove(self, thread_id: str, user_name: str) -> None:
        with self._lock:
            entry = self._threads.get(thread_id)
            if entry is not None and entry.user_name == user_name:
                del self._threads[thread_id]


application_event_broker = ApplicationEventBroker()
