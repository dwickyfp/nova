"""In-memory assistant state (E5a) — threads, messages, and consent grants.

**Nothing here is persisted.** E5a was chosen so there is no credential surface
at rest: a conversation lives in this process only and dies on restart. The
single-database invariant is therefore not touched (there are no
``CONFIG_ASSISTANT_*`` tables in v1).

The consequence is thread affinity: a thread created on worker A is invisible to
worker B. v1 assumes a single web worker or sticky routing; this is stated in
§2 of the design spec and is the revisit trigger for E5b persistence.

Consent (E2b) lives on the thread, so it is scoped to the conversation by
construction and is lost with it.
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
    """Process-local thread store.

    A plain dict guarded by a lock is sufficient: v1 is single-worker (E5a) and
    the critical sections are tiny (create/fetch/append). It is **not** an
    attempt at durable storage.
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

    def clear(self) -> None:
        """Test helper — drop every thread."""
        with self._lock:
            self._threads.clear()


#: Process-wide store. See the module docstring for the single-worker caveat.
thread_store = ThreadStore()
