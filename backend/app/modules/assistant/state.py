"""Runtime assistant state — consent grants keyed by thread.

Threads and messages are **persisted** in ``NOVA_SYSTEM.CONFIG_ASSISTANT_*``
(``repository.py``), so a conversation survives a reload and is scoped per user
in SQL. This module holds the one thing that must stay in process memory:

* **Consent (E2b).** A read-only always-allow grant belongs to a live
  conversation. It is deliberately ephemeral: a restart must not revive a grant,
  so it lives here and dies with the process.

``AssistantThread`` is the runtime object the loop reads (history + consent) and
the router appends to. The router builds one from the repository for a turn and
writes the messages back, so the loop never needs to know about the database.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from app.modules.assistant.schemas import (
    MessageRole,
    ToolCallView,
    ToolClassification,
)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AssistantMessage:
    message_id: str
    role: MessageRole
    content: str = ""
    tool_call: ToolCallView | None = None
    #: Redacted trace and ordered artifacts from a persisted assistant turn.
    #: The context builder folds a bounded summary into follow-up turns.
    steps: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)


@dataclass
class ConsentPolicy:
    """The conversation-scoped grant (E2b).

    ``always_allow_read_only`` is the only grant v1 can hold: it covers the
    ``read_only`` class only. A destructive statement is never auto-approved,
    grant or no grant — see :meth:`covers`.
    """

    always_allow_read_only: bool = False

    def covers(self, classification: ToolClassification) -> bool:
        return self.always_allow_read_only and classification == "read_only"


@dataclass
class AssistantThread:
    thread_id: str
    user_name: str
    title: str
    workspace_file_id: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    messages: list[AssistantMessage] = field(default_factory=list)
    consent: ConsentPolicy = field(default_factory=ConsentPolicy)

    def touch(self) -> None:
        self.updated_at = _now()


class ThreadStore:
    """Runtime thread/consent store.

    Holds the live consent policy per thread. Threads and messages themselves are
    durable in the repository; this store only keeps the ephemeral grant and is
    registered on demand when a turn starts. A plain dict guarded by a lock is
    sufficient: the critical sections are tiny and the value is per-process by
    design.
    """

    def __init__(self) -> None:
        self._threads: dict[str, AssistantThread] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        user_name: str,
        title: str | None = None,
        workspace_file_id: str | None = None,
    ) -> AssistantThread:
        thread = AssistantThread(
            thread_id=str(uuid4()),
            user_name=user_name,
            title=(title or "New conversation").strip() or "New conversation",
            workspace_file_id=workspace_file_id,
        )
        with self._lock:
            self._threads[thread.thread_id] = thread
        return thread

    def register(
        self,
        *,
        thread_id: str,
        user_name: str,
        title: str,
        workspace_file_id: str | None = None,
    ) -> AssistantThread:
        """Adopt a persisted thread into the runtime store (idempotent).

        Called when a turn starts, so the loop has a runtime object for consent
        even after a reload wiped the process. An existing entry is returned
        unchanged, so a live grant is never reset by reopening the thread.
        """
        with self._lock:
            existing = self._threads.get(thread_id)
            if existing is not None and existing.user_name == user_name:
                return existing
            thread = AssistantThread(
                thread_id=thread_id,
                user_name=user_name,
                title=title,
                workspace_file_id=workspace_file_id,
            )
            self._threads[thread_id] = thread
            return thread

    def get(self, thread_id: str, *, user_name: str) -> AssistantThread | None:
        """Fetch a thread, scoped to its owner.

        Returns ``None`` when the thread is unknown **or** owned by another
        user, so the router can answer 404 without leaking existence.
        """
        with self._lock:
            thread = self._threads.get(thread_id)
        if thread is None or thread.user_name != user_name:
            return None
        return thread

    def list_for_user(self, user_name: str) -> list[AssistantThread]:
        with self._lock:
            threads = [t for t in self._threads.values() if t.user_name == user_name]
        return sorted(threads, key=lambda t: t.updated_at, reverse=True)

    def delete(self, thread_id: str, *, user_name: str) -> bool:
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None or thread.user_name != user_name:
                return False
            del self._threads[thread_id]
            return True

    def remove(self, thread_id: str, *, user_name: str) -> None:
        """Drop the runtime entry for a deleted thread (consent goes with it)."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is not None and thread.user_name == user_name:
                del self._threads[thread_id]

    def clear(self) -> None:
        """Test helper — drop every thread."""
        with self._lock:
            self._threads.clear()


#: Process-wide store. See the module docstring for the single-worker caveat.
thread_store = ThreadStore()
