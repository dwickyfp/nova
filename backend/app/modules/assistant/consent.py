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
from typing import BinaryIO


@dataclass(repr=False)
class ConsentApproval:
    """Approved input kept only in the paused request's in-memory future."""

    secure_input: dict[str, str] | None = None
    upload: tuple[str, BinaryIO, str] | None = None


@dataclass
class _PendingConsent:
    future: asyncio.Future[bool | None | ConsentApproval]
    loop: asyncio.AbstractEventLoop
    thread_id: str
    user_name: str
    classification: str = "read_only"
    secure_fields: tuple[str, ...] = ()
    upload_required: bool = False


class ConsentConflictError(RuntimeError):
    """A ``tool_call_id`` was opened while an entry with that id was pending.

    ``open`` refuses a duplicate rather than overwriting the earlier entry: a
    clobbered entry orphans the first turn's future, which then hangs until the
    loop's wall-clock budget expires. The id is model-supplied and a provider
    that reuses one (or a buggy client) must fail cleanly, not silently drop a
    pending decision.
    """

    def __init__(self, tool_call_id: str) -> None:
        super().__init__(f"a consent request for tool_call_id {tool_call_id!r} is already pending")
        self.tool_call_id = tool_call_id


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
    * ``classification`` is the class the loop computed for the pending call, so
      the decision route can validate an ``allow_session`` against it rather
      than trusting the client (NOVA-122, spec §6.1).
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingConsent] = {}
        self._lock = threading.Lock()

    def open(
        self,
        tool_call_id: str,
        *,
        thread_id: str,
        user_name: str,
        classification: str = "read_only",
        secure_fields: tuple[str, ...] = (),
        upload_required: bool = False,
    ) -> asyncio.Future[bool | None | ConsentApproval]:
        """Register a pending decision and return the future to await.

        Raises:
            ConsentConflictError: if ``tool_call_id`` is already pending. The
                existing entry is left untouched, so its owner can still resolve
                it; the colliding caller is told to stop rather than silently
                orphaning the first stream.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool | None | ConsentApproval] = loop.create_future()
        with self._lock:
            if tool_call_id in self._pending:
                raise ConsentConflictError(tool_call_id)
            self._pending[tool_call_id] = _PendingConsent(
                future=future,
                loop=loop,
                thread_id=thread_id,
                user_name=user_name,
                classification=classification,
                secure_fields=secure_fields,
                upload_required=upload_required,
            )
        return future

    def owner_of(self, tool_call_id: str) -> tuple[str, str] | None:
        """``(thread_id, user_name)`` of a still-pending call, or ``None``."""
        with self._lock:
            pending = self._pending.get(tool_call_id)
        return (pending.thread_id, pending.user_name) if pending is not None else None

    def classification_of(self, tool_call_id: str) -> str | None:
        """The classification of a still-pending call, or ``None``."""
        with self._lock:
            pending = self._pending.get(tool_call_id)
        return pending.classification if pending is not None else None

    def secure_fields_of(self, tool_call_id: str) -> tuple[str, ...] | None:
        with self._lock:
            pending = self._pending.get(tool_call_id)
        return pending.secure_fields if pending is not None else None

    def upload_required_of(self, tool_call_id: str) -> bool:
        with self._lock:
            pending = self._pending.get(tool_call_id)
        return bool(pending and pending.upload_required)

    def thread_for(self, tool_call_id: str) -> str | None:
        """The conversation that owns a still-pending call, if any."""
        owner = self.owner_of(tool_call_id)
        return owner[0] if owner is not None else None

    def resolve(
        self,
        tool_call_id: str,
        allowed: bool | None | ConsentApproval,
        *,
        user_name: str,
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


def _set_future(
    future: asyncio.Future[bool | None | ConsentApproval],
    value: bool | None | ConsentApproval,
) -> None:
    if not future.done():
        future.set_result(value)


consent_broker = ConsentBroker()
