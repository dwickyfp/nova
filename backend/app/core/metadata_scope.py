"""One actor-bound metadata transaction per service command.

A service opens the transaction at its boundary with :func:`metadata_transaction`;
repositories it calls, directly or through helpers, join it with
:func:`current_scope`. Nested service calls for the same actor join the outer
transaction, so a domain write and its success audit commit together.

Long flows that call models or engines between metadata reads must not hold
one transaction open. They declare who is acting with :func:`acting_as` (or a
router dependency calls :func:`declare_actor`), and each repository call uses
:func:`scoped_transaction`: it joins this task's open transaction or opens a
short one for the declared actor. Revision checks keep those steps safe.

The scope belongs to the task that opened it. A task spawned inside it does not
inherit the connection: it must open its own transaction, because one
connection cannot serve two concurrent tasks.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi import HTTPException

from app.core.metadata_runtime import metadata_db
from app.modules.access_control.security_context import SecurityContext, SecurityContextError

Connection = psycopg.AsyncConnection[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class MetadataScope:
    connection: Connection
    actor: SecurityContext
    read_only: bool
    task: asyncio.Task[Any] | None


_scope: ContextVar[MetadataScope | None] = ContextVar("nova_metadata_scope", default=None)
_actor: ContextVar[SecurityContext | None] = ContextVar("nova_metadata_actor", default=None)


def _same_actor(left: SecurityContext, right: SecurityContext) -> bool:
    return (
        left.principal == right.principal
        and left.active_role == right.active_role
        and left.security_context_version == right.security_context_version
        and left.principal_type == right.principal_type
        and (left.session_id or "") == (right.session_id or "")
    )


def _task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def active_scope() -> MetadataScope | None:
    """The transaction this task opened, if any."""
    scope = _scope.get()
    if scope is None or scope.task is not _task():
        return None
    return scope


def current_scope() -> MetadataScope:
    scope = active_scope()
    if scope is None:
        raise RuntimeError("This operation requires an active metadata transaction")
    return scope


@asynccontextmanager
async def _open(actor: SecurityContext, read_only: bool) -> AsyncIterator[MetadataScope]:
    async with metadata_db().transaction(actor, read_only=read_only) as connection:
        scope = MetadataScope(connection, actor, read_only, _task())
        token = _scope.set(scope)
        try:
            yield scope
        finally:
            _scope.reset(token)


@asynccontextmanager
async def metadata_transaction(
    actor: SecurityContext, *, read_only: bool = False,
) -> AsyncIterator[MetadataScope]:
    """Join this task's transaction for the same actor, or open one.

    A different actor, or a write inside a read-only transaction, is refused
    rather than silently opening a second authority.
    """
    if not isinstance(actor, SecurityContext):
        raise ValueError("An explicit metadata actor is required")
    scope = active_scope()
    if scope is not None:
        if not _same_actor(scope.actor, actor):
            raise ValueError("A metadata transaction is already open for another actor")
        if scope.read_only and not read_only:
            raise ValueError("A read-only metadata transaction cannot write")
        yield scope
        return
    async with _open(actor, read_only) as opened:
        yield opened


@asynccontextmanager
async def independent_transaction(
    actor: SecurityContext, *, read_only: bool = False,
) -> AsyncIterator[MetadataScope]:
    """A separate transaction that commits on its own, such as a refusal audit."""
    if not isinstance(actor, SecurityContext):
        raise ValueError("An explicit metadata actor is required")
    async with _open(actor, read_only) as opened:
        yield opened


def actor_for(user: Mapping[str, Any]) -> SecurityContext:
    """The caller's metadata actor; a session without one valid role is refused."""
    try:
        return SecurityContext.from_session(dict(user))
    except (SecurityContextError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=403, detail="Session has no valid security context"
        ) from exc


def service_actor(principal: str, active_role: str, *, version: int = 1) -> SecurityContext:
    """An explicit service-principal context for scheduled or system work."""
    return SecurityContext(
        principal=principal,
        active_role=active_role,
        principal_type="SERVICE",
        security_context_version=version,
    )


def acting_actor() -> SecurityContext | None:
    """The actor declared for this work, if any."""
    declared = _actor.get()
    scope = active_scope()
    if scope is not None:
        if declared is not None and not _same_actor(declared, scope.actor):
            raise ValueError("The declared metadata actor differs from the open transaction")
        return scope.actor
    return declared


@contextmanager
def acting_as_sync(actor: SecurityContext) -> Iterator[SecurityContext]:
    """Declare the actor for metadata calls made inside this block."""
    if not isinstance(actor, SecurityContext):
        raise ValueError("An explicit metadata actor is required")
    token = _actor.set(actor)
    try:
        yield actor
    finally:
        _actor.reset(token)


@asynccontextmanager
async def acting_as(actor: SecurityContext) -> AsyncIterator[SecurityContext]:
    with acting_as_sync(actor):
        yield actor


def clear_actor() -> None:
    _actor.set(None)


def declare_actor(user: Mapping[str, Any]) -> SecurityContext:
    """Declare the caller as the actor for the rest of this request's task.

    For an async router dependency: the request task ends with the request, so
    the declaration cannot reach another request.
    """
    clear_actor()
    actor = actor_for(user)
    _actor.set(actor)
    return actor


@asynccontextmanager
async def scoped_transaction(*, read_only: bool = False) -> AsyncIterator[MetadataScope]:
    """Join this task's transaction, or open a short one for the declared actor."""
    scope = active_scope()
    if scope is not None:
        acting_actor()
        if scope.read_only and not read_only:
            raise ValueError("A read-only metadata transaction cannot write")
        yield scope
        return
    actor = _actor.get()
    if actor is None:
        raise RuntimeError("No metadata actor is declared for this operation")
    async with _open(actor, read_only) as opened:
        yield opened
