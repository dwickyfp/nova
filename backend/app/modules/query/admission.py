"""Per-process cap on statements Nova runs at once."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.deps import get_current_user
from app.observability.metrics import QUERY_ADMISSION_ACTIVE, QUERY_ADMISSION_REFUSED

REFUSAL = "Nova is running its maximum number of queries. Retry shortly."


class QueryCapacityError(Exception):
    """The process is already running its configured number of statements."""


class QueryAdmission:
    """Refuse work over ``QUERY_MAX_CONCURRENCY`` instead of queueing it.

    A queued request holds its connection and its caller's patience while the
    engine is still busy with earlier work; refusing lets the gateway send the
    request to another process and lets the client back off.
    """

    def __init__(self) -> None:
        self.active = 0
        self._sources: dict[str, int] = {}

    @asynccontextmanager
    async def slot(
        self, source: str = "web", *, source_limit: int | None = None,
    ) -> AsyncIterator[None]:
        if source_limit is not None and source_limit < 1:
            raise ValueError("Workload concurrency must be positive")
        limit = settings.QUERY_MAX_CONCURRENCY
        if (limit and self.active >= limit) or (
            source_limit is not None and self._sources.get(source, 0) >= source_limit
        ):
            QUERY_ADMISSION_REFUSED.labels(source=source).inc()
            raise QueryCapacityError
        self.active += 1
        self._sources[source] = self._sources.get(source, 0) + 1
        QUERY_ADMISSION_ACTIVE.set(self.active)
        try:
            yield
        finally:
            self.active -= 1
            self._sources[source] -= 1
            if not self._sources[source]:
                del self._sources[source]
            QUERY_ADMISSION_ACTIVE.set(self.active)


query_admission = QueryAdmission()


async def audit_refusal(*, username: str, source: str, target: str, session_id: str | None) -> None:
    """Record a capacity refusal; the statement itself never ran."""
    await write_audit_log(
        event_type="query",
        user_name=username,
        action="execute",
        object_type=source,
        object_name=target,
        status="REFUSED",
        error_message="Query capacity reached",
        session_id=session_id,
    )


async def admitted(
    request: Request, user: Annotated[dict, Depends(get_current_user)]
) -> AsyncIterator[None]:
    """Route dependency: hold an admission slot for the whole request, or 429."""
    try:
        async with query_admission.slot():
            yield
    except QueryCapacityError:
        await audit_refusal(
            username=user["username"],
            source="api",
            target=request.url.path,
            session_id=user.get("session_id"),
        )
        raise HTTPException(
            status_code=429, detail=REFUSAL, headers={"Retry-After": "1"}
        ) from None


Admitted = Depends(admitted)
