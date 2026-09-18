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
    user_name: str


class ConsentBroker:
    """Per-tool-call futures, created by the loop and resolved by the router.

    The pending entry records the owning ``thread_id`` **and** ``user_name``:

    * ``thread_id`` lets an ``allow_session`` decision set the grant on exactly
      the conversation that asked — never on every thread the user owns (E2b).
    * ``user_name`` makes resolution an **owner-scoped** operation. A caller who
      is not the thread's owner must not be able to approve or deny someone
      else's pending call (NOVA-70: the route previously resolved on
      ``tool_call_id`` alone, which is an IDOR — the id is model-supplied and
      not a secret).
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingConsent] = {}
        self._lock = threading.Lock()

    def open(
        self, tool_call_id: str, *, thread_id: str, user_name: str
    ) -> asyncio.Future[bool | None]:
        """Register a pending decision and return the future to await."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool | None] = loop.create_future()
        with self._lock:
            self._pending[tool_call_id] = _PendingConsent(
                future=future, loop=loop, thread_id=thread_id, user_name=user_name
            )
        return future

    def owner_of(self, tool_call_id: str) -> tuple[str, str] | None:
        """``(thread_id, user_name)`` of a still-pending call, or ``None``."""
        with self._lock:
            pending = self._pending.get(tool_call_id)
        return (pending.thread_id, pending.user_name) if pending is not None else None

    def thread_for(self, tool_call_id: str) -> str | None:
        """The conversation that owns a still-pending call, if any."""
        owner = self.owner_of(tool_call_id)
        return owner[0] if owner is not None else None

    def resolve(
        self, tool_call_id: str, allowed: bool | None, *, user_name: str
    ) -> bool:
        """Resolve a pending decision, but only for its owner.

        Returns ``False`` when nothing was waiting **or** the caller is not the
        owner. A foreign caller does not consume the entry: it is left for the
        real owner to resolve. The router turns ``False`` into a 404, matching
        the module's rule that a foreign id must not leak existence.
        """
        with self._lock:
            pending = self._pending.get(tool_call_id)
            if pending is None or pending.user_name != user_name:
                return False
            self._pending.pop(tool_call_id, None)
        if pending.future.done():
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
