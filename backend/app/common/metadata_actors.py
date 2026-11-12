"""Actors and transaction scopes for callers ported to PostgreSQL metadata.

Every metadata read or write runs in an actor-bound transaction from the one
process database (``app.core.metadata_runtime.metadata_db``). A request uses
the caller's :class:`SecurityContext`; scheduled and background work uses
either the original owner's captured context or an explicit SERVICE actor for
its workload. There is no anonymous or root metadata access.

``metadata_scope`` lets a service group several repository calls into one
transaction (definition + job + outbox commit together) while single calls
still get their own short transaction.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg

from app.core.metadata_db import MetadataPermissionDenied
from app.core.metadata_scope import active_scope, metadata_transaction
from app.modules.access_control.security_context import SecurityContext

#: The active role a SERVICE actor reports. Workload grants and forced RLS,
#: not this label, decide what a service may touch.
SERVICE_ROLE = "NOVA_WORKER"

Connection = psycopg.AsyncConnection[dict[str, Any]]


def service_actor(workload: str) -> SecurityContext:
    """The SERVICE actor a workload uses for its own control-plane work."""
    if not workload or not workload.replace("_", "").isalnum():
        raise ValueError("Invalid workload name")
    return SecurityContext(
        principal=f"nova-{workload}",
        active_role=SERVICE_ROLE,
        principal_type="SERVICE",
    )


def owner_actor(
    principal: str | None,
    role: str | None,
    *,
    session_id: str | None = None,
    security_context_version: int | None = None,
) -> SecurityContext:
    """A captured owner context (scheduled graph, queued ML job) as an actor.

    Fails closed: a missing principal or role raises instead of widening to a
    service identity.
    """
    return SecurityContext(
        principal=principal or "",
        active_role=role or "",
        session_id=session_id or None,
        security_context_version=int(security_context_version or 1),
    )


def session_actor(session: dict[str, Any]) -> SecurityContext:
    """The signed-in caller of an HTTP request."""
    return SecurityContext.from_session(session)


@asynccontextmanager
async def metadata_scope(
    actor: SecurityContext,
    *,
    connection: Connection | None = None,
    read_only: bool = False,
) -> AsyncIterator[Connection]:
    """Reuse the caller's open transaction, or open one for this actor."""
    if connection is not None:
        scope = active_scope()
        if scope is None or scope.connection is not connection:
            raise MetadataPermissionDenied()
        async with metadata_transaction(actor, read_only=read_only):
            yield connection
        return
    async with metadata_transaction(actor, read_only=read_only) as scope:
        yield scope.connection
