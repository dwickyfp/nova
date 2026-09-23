"""Bind persisted conversation data to the session that produced it."""

from dataclasses import replace

from fastapi import HTTPException

from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.assistant.state import AssistantThread


def session_security(user: dict) -> SecurityContext:
    try:
        return SecurityContext.from_session(user)
    except (SecurityContextError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=403, detail="Session has no valid security context"
        ) from exc


def observation_context(security: SecurityContext) -> dict:
    return {
        "principal": security.principal,
        "active_role": security.active_role,
        "security_context_version": security.security_context_version,
        "session_id": security.session_id,
    }


def secured_thread(thread: AssistantThread, security: SecurityContext) -> AssistantThread:
    stamp = observation_context(security)
    # Legacy messages and old user text can contain privileged rows too.
    return replace(thread, messages=[m for m in thread.messages if m.security_context == stamp])
