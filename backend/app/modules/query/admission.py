"""Per-process cap on statements the query API runs at once."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import settings


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

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        limit = settings.QUERY_MAX_CONCURRENCY
        if limit and self.active >= limit:
            raise QueryCapacityError
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


query_admission = QueryAdmission()
