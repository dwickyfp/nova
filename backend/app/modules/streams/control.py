"""Who Stream control metadata is written as, and the transaction it uses.

Stream control state (generations, definitions, sources, the commit index,
claims, consumption facts and receipts) lives in PostgreSQL and is read and
written only through :class:`StreamMetadataRepository` in an actor-bound
transaction. Each call is its own short transaction, so every durable step of
the consumption protocol (prepare, claim, submit decision, receipt, offset)
commits before the next one starts, as the protocol requires.

The actor is explicit at the service boundary: a Stream entry point that has a
caller binds it with :func:`acting_as`, and everything it calls records that
principal. Work without a caller (scheduler maintenance, trigger planning,
the write-routing cache) acts as the ``streams`` SERVICE actor. Stream
metadata authorization is the owning service's (and Ranger's, for the source
data); the database requires only a bound actor of the calling workload.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar

from app.common.metadata_actors import metadata_scope, service_actor
from app.modules.access_control.security_context import SecurityContext, SecurityContextError
from app.modules.streams.metadata_repository import StreamMetadataRepository
from app.modules.streams.schemas import StreamError

_actor: ContextVar[SecurityContext | None] = ContextVar("nova_stream_actor", default=None)

MetadataScope = Callable[..., AbstractAsyncContextManager[StreamMetadataRepository]]


def caller_actor(username: str, role: str | None, version: int | None = 1) -> SecurityContext:
    """The caller of a Stream statement as a metadata actor; ambiguity is refused."""
    try:
        return SecurityContext(
            principal=username or "",
            active_role=role or "",
            security_context_version=int(version or 1),
        )
    except SecurityContextError:
        raise StreamError("STREAM_ACCESS_DENIED", "Select an active role to use streams") from None


def current_actor() -> SecurityContext:
    return _actor.get() or service_actor("streams")


@contextmanager
def acting_as(actor: SecurityContext) -> Iterator[None]:
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


@asynccontextmanager
async def stream_metadata(*, read_only: bool = False) -> AsyncIterator[StreamMetadataRepository]:
    """One transaction for the bound actor (or the streams service actor)."""
    actor = current_actor()
    async with metadata_scope(actor, read_only=read_only) as connection:
        yield StreamMetadataRepository(connection, actor)
