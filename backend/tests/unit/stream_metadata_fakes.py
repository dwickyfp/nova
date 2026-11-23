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


#: The access types of the StarRocks Ranger service definition (Ranger 2.8,
#: as read from the governed stack on 2026-11-23).
STARROCKS_ACCESS_TYPES = tuple(sorted((
    "grant", "node", "operate", "delete", "drop", "insert", "select", "alter", "export",
    "update", "usage", "plugin", "file", "blacklist", "repository", "refresh", "impersonate",
    "create database", "create table", "create view", "create function",
    "create global function", "create materialized view", "create resource",
    "create resource group", "create external catalog", "create storage volume",
)))
