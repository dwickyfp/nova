"""Namespace admission is separate from provider and authorization implementations."""

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import uuid4

from app.modules.streams.namespace import StreamName
from app.modules.streams.repository import StreamRecord, StreamRepository
from app.modules.streams.schemas import ChangeCursor, StreamError, operation_id


@dataclass(frozen=True, slots=True)
class SourceHead:
    source_id: str
    cursor: ChangeCursor


class StreamAccess(Protocol):
    async def namespace(self, name: StreamName) -> None: ...
    async def collision(self, name: StreamName) -> bool: ...
    async def source(self, name: StreamName) -> SourceHead: ...
    async def stream(self, record: StreamRecord) -> None: ...


class StreamCatalog:
    def __init__(self, repository: StreamRepository, access: StreamAccess) -> None:
        self.repository = repository
        self.access = access

    async def _namespace(self, name: StreamName) -> None:
        await self.access.namespace(name)
        if await self.access.collision(name):
            raise StreamError("STREAM_NAME_COLLISION", "A table or view occupies this name")

    async def get(self, name: StreamName) -> StreamRecord:
        await self._namespace(name)
        record = await self.repository.current(name)
        if record is None or record.status == "DROPPED":
            raise StreamError("STREAM_NOT_FOUND", "Stream does not exist or is not accessible")
        await self.access.stream(record)
        return record

    async def create(
        self, name: StreamName, source: StreamName, owner_role: str, *, if_not_exists=False,
    ) -> StreamRecord:
        await self._namespace(name)
        current = await self.repository.current(name)
        if current and current.status != "DROPPED":
            await self.access.stream(current)
            if if_not_exists:
                return current
            raise StreamError("STREAM_ALREADY_EXISTS", "Stream already exists")
        if not owner_role:
            raise StreamError("STREAM_ACCESS_DENIED", "An active owner role is required")
        head = await self.access.source(source)
        record = StreamRecord(
            name, current.generation + 1 if current else 0, operation_id(uuid4().hex),
            source, head.source_id, owner_role, head.cursor,
        )
        result = await self.repository.publish(record)
        # Engine DDL does not participate in Nova's namespace claim.
        await self._namespace(name)
        return result

    async def drop(self, name: StreamName, *, if_exists=False) -> None:
        await self._namespace(name)
        record = await self.repository.current(name)
        if record is None or record.status == "DROPPED":
            if if_exists:
                return
            raise StreamError("STREAM_NOT_FOUND", "Stream does not exist or is not accessible")
        await self.access.stream(record)
        await self.repository.publish(replace(
            record, generation=record.generation + 1, status="DROPPED",
        ))
