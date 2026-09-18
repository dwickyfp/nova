"""Consent broker — bridges the SSE stream and the separate decision request.

The stream pauses on a proposed tool call; the client resolves it with
``POST /tool-calls/{id}/decision``. SSE is one-way, so the two directions are
joined here: the loop awaits a future, the decision route resolves it.

This is process-local like the rest of assistant state (E5a). A thread whose
stream has ended leaves no broker entry behind — the wait is bounded by the
loop's own budget.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass


@dataclass
class _PendingConsent:
    future: asyncio.Future[bool | None]
    loop: asyncio.AbstractEventLoop
    thread_id: str


class ConsentBroker:
    """Per-tool-call futures, created by the loop and resolved by the router.

    The pending entry also records the owning ``thread_id`` so an
    ``allow_session`` decision can set the grant on exactly the conversation
    that asked — never on every thread the user owns (E2b).
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingConsent] = {}
        self._lock = threading.Lock()

    def open(self, tool_call_id: str, *, thread_id: str) -> asyncio.Future[bool | None]:
        """Register a pending decision and return the future to await."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool | None] = loop.create_future()
        with self._lock:
            self._pending[tool_call_id] = _PendingConsent(
                future=future, loop=loop, thread_id=thread_id
            )
        return future

    def thread_for(self, tool_call_id: str) -> str | None:
        """The conversation that owns a still-pending call, if any."""
        with self._lock:
            pending = self._pending.get(tool_call_id)
        return pending.thread_id if pending is not None else None

    def resolve(self, tool_call_id: str, allowed: bool | None) -> bool:
        """Resolve a pending decision. ``False`` when nothing was waiting."""
        with self._lock:
            pending = self._pending.pop(tool_call_id, None)
        if pending is None or pending.future.done():
            return False
        pending.loop.call_soon_threadsafe(_set_future, pending.future, allowed)
        return True

    def cancel_all(self) -> None:
        """Resolve every waiter with ``None`` (client gone / shutdown)."""
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for _, entry in pending:
            if not entry.future.done():
                entry.loop.call_soon_threadsafe(_set_future, entry.future, None)

    def clear(self) -> None:
        """Test helper."""
        with self._lock:
            self._pending.clear()


def _set_future(future: asyncio.Future[bool | None], value: bool | None) -> None:
    if not future.done():
        future.set_result(value)


consent_broker = ConsentBroker()
