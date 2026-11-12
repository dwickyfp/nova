"""Audit helpers for Nova user actions.

Events go to the PostgreSQL audit log through
:class:`app.common.metadata_audit.AuditRepository`. A successful governed
action that is already inside this task's metadata transaction for the same
actor is recorded in that transaction, so the change and its audit commit or
roll back together. Every other event, including refusals and failures, is
written in its own transaction and therefore survives the caller's rollback.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from app.common.audit_text import AUDIT_TEXT_MAX_BYTES as AUDIT_TEXT_MAX_BYTES
from app.common.audit_text import fit_audit_text as fit_audit_text
from app.common.metadata_audit import AuditEvent, AuditIdentity, AuditRepository
from app.common.sql_guard import redact_sql_credentials
from app.core.metadata_scope import acting_actor, active_scope, independent_transaction
from app.modules.access_control.security_context import SecurityContext, SecurityContextError

#: Recorded as the active role of an event that has none: a sign-in attempt,
#: a refusal before role activation or a system step acting for a person.
#: Every StarRocks user holds PUBLIC, so it claims no privilege.
NO_ACTIVE_ROLE = "PUBLIC"
#: Recorded as the principal of an event that names nobody.
ANONYMOUS = "anonymous"


def audit_actor(
    user_name: str | None,
    active_role: str | None,
    security_context_version: int | None = None,
    session_id: str | None = None,
) -> SecurityContext:
    """The identity an event is recorded under.

    The event's own subject is the principal; it is never replaced by another
    user. ``root`` is a service identity. A missing or malformed role is
    recorded as :data:`NO_ACTIVE_ROLE`.
    """
    principal = (user_name or "").strip()[:128] or ANONYMOUS
    scope = active_scope()
    bound = scope.actor if scope is not None else acting_actor()
    if (
        bound is not None
        and bound.principal == principal
        and bound.active_role == (active_role or "").strip()
        and (session_id is None or session_id == bound.session_id)
        and (
            security_context_version is None
            or security_context_version == bound.security_context_version
        )
    ):
        return bound
    principal_type: Literal["USER", "SERVICE"] = (
        "SERVICE" if principal.casefold() == "root" else "USER"
    )
    version = security_context_version if isinstance(security_context_version, int) else 1
    session = (
        session_id
        if (
            isinstance(session_id, str)
            and session_id.strip()
            and len(session_id) <= 64
            and "\0" not in session_id
        )
        else None
    )
    try:
        return SecurityContext(
            principal=principal,
            active_role=(active_role or "").strip() or NO_ACTIVE_ROLE,
            principal_type=principal_type,
            session_id=session,
            security_context_version=max(1, version),
        )
    except SecurityContextError:
        return SecurityContext(
            principal=principal,
            active_role=NO_ACTIVE_ROLE,
            principal_type=principal_type,
            session_id=session,
            security_context_version=max(1, version),
        )


def _clean(value: str | None) -> str | None:
    """PostgreSQL text cannot hold NUL; keep the rest of the text unchanged."""
    return value.replace("\0", "\ufffd") if value else None


def _joins(scope_actor: SecurityContext, actor: SecurityContext) -> bool:
    return (
        scope_actor.principal == actor.principal
        and scope_actor.active_role == actor.active_role
        and scope_actor.security_context_version == actor.security_context_version
        and scope_actor.principal_type == actor.principal_type
        and scope_actor.session_id == actor.session_id
    )


async def _persist(
    actor: SecurityContext,
    identity: AuditIdentity,
    event: AuditEvent,
    *,
    success: bool,
) -> None:
    scope = active_scope()
    if success and scope is not None and not scope.read_only and _joins(scope.actor, actor):
        await AuditRepository(scope.connection).append(identity, event)
        return
    async with independent_transaction(actor) as own:
        await AuditRepository(own.connection).append(identity, event)


async def write_audit_log(
    *,
    event_type: str,
    user_name: str,
    action: str,
    object_type: str,
    object_name: str,
    status: str,
    sql_text: str | None = None,
    rewritten_sql: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    rows_affected: int | None = None,
    session_id: str | None = None,
    file_id: str | None = None,
    database_name: str | None = None,
    schema_name: str | None = None,
    active_role: str | None = None,
    security_context_version: int | None = None,
    decision: str | None = None,
    ranger_policy_ids: str | None = None,
    query_id: str | None = None,
    nova_execution_id: str | None = None,
    engine_query_ids: list[str] | None = None,
) -> str:
    """Write an audit log entry. Returns the query_id (UUID) for the entry."""
    from app.modules.query_autopilot.telemetry import EXECUTION, PURPOSE

    execution = EXECUTION.get()
    if execution is not None:
        nova_execution_id = nova_execution_id or execution.id
        engine_query_ids = engine_query_ids if engine_query_ids is not None else execution.query_ids
    qid = query_id or str(uuid.uuid4())
    actor = audit_actor(user_name, active_role, security_context_version, session_id)
    event = AuditEvent(
        event_type=event_type,
        action=action or "-",
        object_type=object_type or "-",
        object_name=(_clean(object_name) or "-")[:512],
        status=status,
        query_id=qid,
        # AuditEvent applies the credential redactor and the byte budget.
        sql_text=_clean(sql_text),
        rewritten_sql=_clean(rewritten_sql),
        error_message=_clean(error_message),
        duration_ms=duration_ms,
        rows_affected=rows_affected,
        session_id=actor.session_id,
        file_id=file_id,
        database_name=database_name,
        schema_name=schema_name,
        decision=decision,
        ranger_policy_ids=ranger_policy_ids,
        nova_execution_id=nova_execution_id,
        engine_query_ids=tuple(engine_query_ids) if engine_query_ids else None,
        execution_purpose=PURPOSE.get() if execution else None,
    )
    await _persist(
        actor,
        AuditIdentity(uuid.uuid4(), datetime.now(UTC)),
        event,
        success=status.upper() == "SUCCESS",
    )
    if execution is not None:
        execution.audit_ids.append(qid)
    return qid


__all__ = [
    "AUDIT_TEXT_MAX_BYTES",
    "NO_ACTIVE_ROLE",
    "audit_actor",
    "fit_audit_text",
    "redact_sql_credentials",
    "write_audit_log",
]
