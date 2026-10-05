"""Immutable namespace generations; primary-key upserts are never used as CAS."""

from contextlib import suppress
from dataclasses import dataclass
from uuid import uuid4

from app.modules.streams.claims import Claim, ClaimOutcome, ClaimRepository, MetadataExecutor
from app.modules.streams.namespace import StreamName
from app.modules.streams.schemas import ChangeCursor, StreamError, operation_id

STREAMS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STREAMS (
    catalog_name VARCHAR(128) NOT NULL,
    database_name VARCHAR(128) NOT NULL,
    schema_name VARCHAR(128) NOT NULL,
    name VARCHAR(128) NOT NULL,
    generation BIGINT NOT NULL,
    stream_id VARCHAR(64) NOT NULL,
    source_database VARCHAR(128) NOT NULL,
    source_name VARCHAR(128) NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    owner_role VARCHAR(128) NOT NULL,
    cursor_epoch BIGINT NOT NULL,
    cursor_sequence BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(catalog_name, database_name, schema_name, name, generation)
DISTRIBUTED BY HASH(catalog_name, database_name, schema_name, name) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""


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


_COLUMNS = (
    "catalog_name, database_name, schema_name, name, generation, stream_id, "
    "source_database, source_name, source_id, owner_role, cursor_epoch, cursor_sequence, status"
)
_SCOPE = "catalog_name=%s AND database_name=%s AND schema_name=%s AND name=%s"


class StreamRepository:
    def __init__(self, execute: MetadataExecutor | None = None) -> None:
        if execute is None:
            from app.core.database import db

            execute = db.execute_system
        self.execute = execute
        self.claims = ClaimRepository(execute)

    async def current(self, name: StreamName) -> StreamRecord | None:
        result = await self.execute(
            f"SELECT {_COLUMNS} FROM NOVA_SYSTEM.CONFIG_STREAMS WHERE {_SCOPE} "
            "ORDER BY generation DESC LIMIT 1", name.key,
        )
        if not result["rows"]:
            return None
        row = result["rows"][0]
        record = StreamRecord(
            StreamName(row[1], row[3], row[2], row[0]), row[4], row[5],
            StreamName(row[6], row[7]), row[8], row[9], ChangeCursor(row[10], row[11]), row[12],
        )
        if record.name != name:
            raise StreamError("STREAM_METADATA_INCONSISTENT", "Stream identity is inconsistent")
        return record

    async def publish(self, record: StreamRecord) -> StreamRecord:
        """Only the claim winner can publish; uncertainty blocks a new operation."""
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
        label = "nova_stream_namespace_" + claim.key
        # Exact immutable readback acknowledges publication after response loss.
        with suppress(Exception):
            await self.execute(
                f"INSERT INTO NOVA_SYSTEM.CONFIG_STREAMS ({_COLUMNS}, created_at) "
                f"WITH LABEL {label} SELECT " + ", ".join(["%s"] * 13) + ", NOW() "
                "WHERE NOT EXISTS (SELECT 1 FROM NOVA_SYSTEM.CONFIG_STREAMS "
                f"WHERE {_SCOPE} AND generation=%s)",
                (*record.values, *record.name.key, record.generation),
            )
        if await self.current(record.name) != record:
            raise StreamError("STREAM_NAMESPACE_BUSY", "Stream namespace requires verification")
        return record
