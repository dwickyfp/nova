"""Who is acting on a workspace, for metadata access and audit.

The workspace service is keyed by namespace: a username for a personal
workspace, ``~ws/<id>`` for a shared one. Workspace metadata is read and written
as the authenticated person, with the roles assigned to their session, so the
API layer (or a worker acting for a runtime's live session) records that
session here for the length of a request or job. Audit rows name the person,
never the shared namespace.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.core.exceptions import StorageError

_SESSION: ContextVar[Mapping[str, Any] | None] = ContextVar("workspace_session", default=None)


def acting_user(namespace: str) -> str:
    """The person acting, or the namespace itself (a personal workspace)."""
    session = _SESSION.get()
    return str(session["username"]) if session and session.get("username") else namespace


def acting_session() -> Mapping[str, Any]:
    """The authenticated session workspace metadata is accessed as."""
    session = _SESSION.get()
    if session is None:
        raise StorageError("Sign in again to open your workspace", status_code=401)
    return session


def declare_session(session: Mapping[str, Any]) -> None:
    """Act as ``session`` for the rest of this request's task."""
    _SESSION.set(dict(session))


@contextmanager
def acting_as(session: Mapping[str, Any]) -> Iterator[None]:
    """Act as ``session`` inside this block."""
    if not isinstance(session, Mapping) or not session.get("username"):
        raise ValueError("A workspace session is required")
    token = _SESSION.set(dict(session))
    try:
        yield
    finally:
        _SESSION.reset(token)
