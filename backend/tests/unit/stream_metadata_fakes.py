"""A Stream metadata scope that yields a mocked repository, for unit tests."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock


def metadata_scope(repository=None):
    """``(scope, repository)``: every transaction yields the same mock repository."""
    repository = repository or AsyncMock()
    transactions = []

    @asynccontextmanager
    async def scope(*, read_only=False):
        transactions.append(read_only)
        yield repository

    scope.transactions = transactions
    return scope, repository
