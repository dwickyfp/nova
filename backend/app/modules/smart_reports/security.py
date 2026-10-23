"""Recheck the live authentication boundary before releasing report data."""

from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import HTTPException

from app.core.redis import session_store
from app.modules.assistant.security import session_security

_scheduled_scope: ContextVar[str | None] = ContextVar("report_scheduled_scope", default=None)


@contextmanager
def scheduled_execution(user: dict):
    """Issued only inside the orchestrator's verified delegated connection."""
    from app.modules.query.service import delegated_connection_for

    if delegated_connection_for(user["username"]) is None:
        raise HTTPException(403, "A verified scheduled connection is required")
    token = _scheduled_scope.set(session_security(user).cache_partition())
    try:
        yield
    finally:
        _scheduled_scope.reset(token)


async def require_current_session(user: dict) -> None:
    expected = session_security(user)
    if _scheduled_scope.get() == expected.cache_partition():
        from app.modules.query.service import delegated_connection_for
        from app.modules.task_orchestration.repository import task_orchestration_repository

        bound = await task_orchestration_repository.get_role_execution_user(expected.active_role)
        if bound == expected.principal and delegated_connection_for(expected.principal) is not None:
            return
        raise HTTPException(403, "Scheduled execution binding was revoked")
    if not expected.session_id:
        raise HTTPException(403, "A current authenticated session is required")
    current = await session_store.get(expected.session_id)
    if current is None:
        raise HTTPException(401, "Session expired")
    actual = session_security({**current, "session_id": expected.session_id})
    if actual != expected:
        raise HTTPException(409, "Security context changed; reopen the report")
