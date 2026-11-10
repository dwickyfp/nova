"""Who is acting on a workspace, for audit.

The workspace service is keyed by namespace: a username for a personal
workspace, ``~ws/<id>`` for a shared one. Audit rows must name the person, so
the API layer records them here for the length of a request.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_ACTOR: ContextVar[str | None] = ContextVar("workspace_actor", default=None)


def acting_user(namespace: str) -> str:
    """The person acting, or the namespace itself (a personal workspace)."""
    return _ACTOR.get() or namespace


@contextmanager
def acting_as(username: str) -> Iterator[None]:
    token = _ACTOR.set(username)
    try:
        yield
    finally:
        _ACTOR.reset(token)
