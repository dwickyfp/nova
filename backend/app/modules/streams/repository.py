"""Immutable namespace generations, published only by their claim winner."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.metadata_db import (
    MetadataConflict,
    MetadataIntegrityViolation,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
)
from app.modules.streams.claims import Claim, ClaimOutcome, ClaimRepository
from app.modules.streams.control import MetadataScope, stream_metadata
from app.modules.streams.metadata_repository import NAMESPACE_FIELDS
from app.modules.streams.namespace import StreamName
from app.modules.streams.schemas import ChangeCursor, StreamError, operation_id

logger = logging.getLogger(__name__)

#: A fact the database refused or whose commit outcome is unknown: only a
#: fresh readback can tell what is durable.
_UNVERIFIED = (
    MetadataConflict, MetadataIntegrityViolation, MetadataOutcomeUnknown,
    MetadataOperationFailed,
)


@dataclass(frozen=True, slots=True)
class StreamRecord:
    name: StreamName
    generation: int
    stream_id: str
    source: StreamName
    source_id: str
    owner_role: str
    cursor: ChangeCursor
    status: str = "READY"

    @property
    def values(self) -> tuple:
        return (*self.name.key, self.generation, self.stream_id, self.source.database,
                self.source.name, self.source_id, self.owner_role,
                self.cursor.epoch, self.cursor.sequence, self.status)


def _mapping(record: StreamRecord) -> dict[str, Any]:
    return dict(zip(NAMESPACE_FIELDS, record.values, strict=True))


def _decode_record(row: dict[str, Any]) -> StreamRecord:
    record = StreamRecord(
        StreamName(row["database_name"], row["name"], row["schema_name"], row["catalog_name"]),
        int(row["generation"]), row["stream_id"],
        StreamName(row["source_database"], row["source_name"]), row["source_id"],
        row["owner_role"], ChangeCursor(int(row["cursor_epoch"]), int(row["cursor_sequence"])),
        row["status"],
    )
    if record.generation < 0 or record.status not in {"READY", "DROPPED", "STALE"}:
        raise StreamError("STREAM_METADATA_INCONSISTENT", "Stream generation is inconsistent")
    return record


class StreamRepository:
    def __init__(self, metadata: MetadataScope = stream_metadata) -> None:
        self._metadata = metadata
        self.claims = ClaimRepository(metadata)

    async def current(self, name: StreamName) -> StreamRecord | None:
        async with self._metadata(read_only=True) as repository:
            row = await repository.current(name.key)
        if row is None:
            return None
        record = _decode_record(row)
        if record.name != name:
            raise StreamError("STREAM_METADATA_INCONSISTENT", "Stream identity is inconsistent")
        return record

    async def current_generations(
        self, *, database: str | None = None, source_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """The newest generation of every stream name, with its definition mode."""
        async with self._metadata(read_only=True) as repository:
            return await repository.current_generations(
                database=database, source_id=source_id, statuses=statuses,
            )

    async def _prepare(self, record: StreamRecord) -> None:
        digest = operation_id(*record.values)
        try:
            async with self._metadata() as repository:
                await repository.prepare_namespace_operation(digest, _mapping(record))
        except _UNVERIFIED:
            logger.debug("namespace facts need readback", exc_info=True)
        if await self._operation(digest) != record:
            raise StreamError("STREAM_NAMESPACE_BUSY", "Namespace operation requires verification")

    async def _operation(self, digest: str) -> StreamRecord:
        async with self._metadata(read_only=True) as repository:
            row = await repository.namespace_operation(digest)
        if row is None:
            raise StreamError("STREAM_NAMESPACE_BUSY", "Namespace operation facts are unavailable")
        record = _decode_record(row)
        if operation_id(*record.values) != digest:
            raise StreamError("STREAM_METADATA_INCONSISTENT", "Namespace operation is inconsistent")
        return record

    async def publish(self, record: StreamRecord) -> StreamRecord:
        """Only the claim winner can publish; uncertainty blocks a new operation."""
        await self._prepare(record)
        digest = operation_id(*record.values)
        claim = Claim(
            operation_id("stream_namespace", *record.name.key), 0, record.generation,
            operation_id(uuid4().hex), digest,
        )
        result = await self.claims.acquire(claim)
        if result.outcome != ClaimOutcome.ACQUIRED:
            current = await self.current(record.name)
            if current == record:
                return current
            raise StreamError("STREAM_NAMESPACE_BUSY", "Stream namespace requires verification")
        return await self._publish_winner(record)

    async def recover_namespace(self, name: StreamName, generation: int) -> StreamRecord:
        """Repair publication from durable winner facts, never from retry input."""
        lookup = Claim(operation_id("stream_namespace", *name.key), 0, generation, "0" * 64,
                       "0" * 64)
        result = await self.claims.inspect(lookup)
        if result.winner is None:
            raise StreamError("STREAM_NAMESPACE_BUSY", "Namespace claim requires verification")
        winner = result.winner
        record = await self._operation(winner.operation_digest)
        if record.name != name or record.generation != generation:
            raise StreamError("STREAM_METADATA_INCONSISTENT", "Namespace claim is inconsistent")
        current = await self.current(name)
        if current is not None and current.generation >= generation:
            if current.generation == generation and current != record:
                raise StreamError("STREAM_METADATA_INCONSISTENT", "Namespace publication conflicts")
            return current
        if generation != (current.generation + 1 if current else 0):
            raise StreamError("STREAM_METADATA_INCONSISTENT", "Namespace generation is missing")
        return await self._publish_winner(record)

    async def _publish_winner(self, record: StreamRecord) -> StreamRecord:
        # The database refuses a publication without the winning claim and a
        # different fact for the generation; an exact readback acknowledges a
        # publication whose commit response was lost.
        try:
            async with self._metadata() as repository:
                await repository.publish_generation(_mapping(record))
        except _UNVERIFIED:
            logger.debug("namespace publication needs readback", exc_info=True)
        if await self.current(record.name) != record:
            raise StreamError("STREAM_NAMESPACE_BUSY", "Stream namespace requires verification")
        return record
