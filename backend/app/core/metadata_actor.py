"""The actor a metadata transaction is bound to.

The request boundary (``get_current_user``) binds the caller's
:class:`SecurityContext`; a worker, automation or scheduled path binds its own
explicit identity with :func:`acting_as`. Services open actor-bound transactions
with :func:`metadata_transaction`. There is no implicit service identity: with
nothing bound, metadata access is refused rather than performed as someone else.

Tasks created while an actor is bound inherit it (``asyncio`` copies the
context), so a turn streamed after the request handler returns still runs as
the caller who started it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import psycopg
from fastapi import HTTPException

from app.core import metadata_scope
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext, SecurityContextError

#: Owner of the platform tool and connector catalog. It is a service principal;
#: reading the catalog never borrows a person's identity.
PLATFORM_PRINCIPAL = "__nova__"
PLATFORM_ROLE = "PUBLIC"


def bind_request_actor(user: dict[str, Any]) -> SecurityContext | None:
    """Bind the authenticated session's actor for the rest of this request.

    A session without exactly one valid active role binds nothing, so any
    metadata access on that request fails closed.
    """
    try:
        return metadata_scope.declare_actor(user)
    except (HTTPException, SecurityContextError, TypeError, ValueError):
        return None


@contextmanager
def acting_as(actor: SecurityContext) -> Iterator[SecurityContext]:
    """Run a block (worker, automation, scheduled job) as an explicit actor."""
    if not isinstance(actor, SecurityContext):
        raise ValueError("An explicit metadata actor is required")
    with metadata_scope.acting_as_sync(actor):
        yield actor


def current_actor() -> SecurityContext:
    actor = metadata_scope.acting_actor()
    if actor is None:
        raise MetadataPermissionDenied()
    return actor


def bound_actor() -> SecurityContext | None:
    return metadata_scope.acting_actor()


def actor_for(principal: str) -> SecurityContext:
    """The bound actor, which must be ``principal``; the platform catalog is a service."""
    if principal == PLATFORM_PRINCIPAL:
        return platform_actor()
    actor = current_actor()
    if actor.principal != principal:
        raise MetadataPermissionDenied()
    return actor


def platform_actor() -> SecurityContext:
    return SecurityContext(PLATFORM_PRINCIPAL, PLATFORM_ROLE, principal_type="SERVICE")


def service_actor(principal: str, role: str) -> SecurityContext:
    return SecurityContext(principal, role, principal_type="SERVICE")


@asynccontextmanager
async def metadata_transaction(
    actor: SecurityContext | None = None, *, read_only: bool = False,
) -> AsyncIterator[tuple[psycopg.AsyncConnection[dict[str, Any]], SecurityContext]]:
    """One actor-bound PG transaction; the bound actor unless one is given."""
    actor = actor or current_actor()
    async with metadata_scope.metadata_transaction(actor, read_only=read_only) as scope:
        yield scope.connection, scope.actor
