"""A stand-in metadata database for unit tests of ported services.

Unit tests mock repositories, not SQL. This fake only records which actor
opened each transaction and whether it committed, so a test can assert the
service boundary: one actor-bound transaction per command, read-only reads,
and a rollback when the command fails.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core import metadata_runtime


@dataclass
class FakeTransaction:
    actor: Any
    read_only: bool
    connection: Any = field(default_factory=MagicMock)
    outcome: str = "open"


class FakeMetadataDatabase:
    def __init__(self) -> None:
        self.transactions: list[FakeTransaction] = []

    @asynccontextmanager
    async def transaction(self, actor, *, read_only: bool = False, request_id: str = ""):
        record = FakeTransaction(actor, read_only)
        self.transactions.append(record)
        try:
            yield record.connection
        except BaseException:
            record.outcome = "rolled_back"
            raise
        record.outcome = "committed"

    @property
    def committed(self) -> list[FakeTransaction]:
        return [item for item in self.transactions if item.outcome == "committed"]


@pytest.fixture
def fake_metadata_db():
    database = FakeMetadataDatabase()
    metadata_runtime._database = None
    metadata_runtime.install_metadata_db(database)  # type: ignore[arg-type]
    try:
        yield database
    finally:
        metadata_runtime.uninstall_metadata_db(database)  # type: ignore[arg-type]
