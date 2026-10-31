"""The signed-in Nova session of the request or task being served.

Kept in context variables rather than ``flask.g``: Superset runs synchronous
SQL Lab queries inside a fresh ``test_request_context``, which starts with an
empty ``g``, but stays on the same thread and context. Every request clears
these values first and again at teardown, so a worker thread never carries one
user's session into the next request.

A Celery task body does not run in the context its ``task_prerun`` signal ran
in, so the task's session is also kept per process. The worker uses the
prefork pool, where a process runs one task at a time; ``task_postrun`` clears
it. Read the session only through the accessors below, which check both.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from nova_superset.client import DataToken, Identity

bi_handle: ContextVar[str | None] = ContextVar("nova_bi_handle", default=None)
identity: ContextVar[Identity | None] = ContextVar("nova_identity", default=None)
data_token: ContextVar[DataToken | None] = ContextVar("nova_data_token", default=None)
#: Set inside a Celery task. Only there may a missing session be replaced by a
#: delegated one; a web request without a Nova session is simply signed out.
background: ContextVar[bool] = ContextVar("nova_background", default=False)
#: Set for tasks that must run as the session that submitted them (async SQL
#: Lab): they are refused rather than delegated when that session is gone.
carry_required: ContextVar[bool] = ContextVar("nova_carry_required", default=False)

#: The running Celery task's session in this (prefork) worker process.
_task: dict[str, Any] = {}


def clear() -> None:
    bi_handle.set(None)
    identity.set(None)
    data_token.set(None)
    background.set(False)
    carry_required.set(False)


def start_task(*, required: bool) -> None:
    clear()
    _task.clear()
    _task.update(background=True, carry_required=required)
    background.set(True)
    carry_required.set(required)


def end_task() -> None:
    _task.clear()
    clear()


def adopt(handle: str, who: Identity) -> None:
    """Make ``handle`` the session of the current request or task."""
    bi_handle.set(handle)
    identity.set(who)
    data_token.set(None)
    if _task:
        _task.update(handle=handle, identity=who, data_token=None)


def current_handle() -> str | None:
    return bi_handle.get() or _task.get("handle")


def current_identity() -> Identity | None:
    return identity.get() or _task.get("identity")


def current_token() -> DataToken | None:
    return data_token.get() or _task.get("data_token")


def remember_token(token: DataToken) -> None:
    data_token.set(token)
    if _task:
        _task["data_token"] = token


def in_background() -> bool:
    return background.get() or bool(_task.get("background"))


def needs_carry() -> bool:
    return carry_required.get() or bool(_task.get("carry_required"))


def partition_key() -> str | None:
    """Who a cached result or thumbnail belongs to: principal, role, version."""
    current = current_identity()
    if current is None:
        return None
    kind = "delegated" if current.delegated else "session"
    return (
        f"nova:{kind}:{current.username}:{current.active_role}:{current.security_context_version}"
    )
