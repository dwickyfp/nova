"""Real-engine namespace persistence, isolated from the deployment's NOVA_SYSTEM."""

import asyncio
from dataclasses import replace

import pytest

from app.modules.streams.namespace import StreamName
from app.modules.streams.repository import (
    StreamRecord,
    StreamRepository,
)
from app.modules.streams.schemas import ChangeCursor, StreamError, operation_id
from tests.integration import test_stream_durability_gate as gate

connect = gate.connect
source = gate.source

pytestmark = pytest.mark.engine


async def test_fresh_upgrade_repeat_and_concurrent_namespace_claim(source):
    name = StreamName(source, "orders_stream")
    record = StreamRecord(
        name,
        0,
        operation_id("first"),
        StreamName(source, "target"),
        operation_id("source"),
        "reader",
        ChangeCursor(3, 42),
    )
    repositories = [StreamRepository(), StreamRepository()]
    outcomes = await asyncio.gather(
        repositories[0].publish(record),
        repositories[1].publish(replace(record, stream_id=operation_id("second"))),
        return_exceptions=True,
    )
    assert sum(isinstance(value, StreamRecord) for value in outcomes) == 1
    assert sum(isinstance(value, StreamError) for value in outcomes) == 1
    winner = await repositories[0].current(name)
    assert winner is not None
    assert await repositories[1].current(name) == winner
    assert await repositories[0].current(StreamName("another_database", name.name)) is None

    dropped = replace(winner, generation=1, status="DROPPED")
    await repositories[0].publish(dropped)
    recreated = replace(winner, generation=2, stream_id=operation_id("recreated"))
    await repositories[1].publish(recreated)
    assert await repositories[0].current(name) == recreated

    # An old worker may verify its old claim, but may not replace the new identity.
    with pytest.raises(StreamError):
        await repositories[0].publish(winner)
    assert await repositories[0].current(name) == recreated


async def test_crash_after_claim_recovers_immutable_namespace_winner(source, monkeypatch):
    record = StreamRecord(
        StreamName(source, "crash_stream"),
        0,
        operation_id("winner"),
        StreamName(source, "target"),
        operation_id("source"),
        "reader",
        ChangeCursor(1, 0),
    )
    writer = StreamRepository()

    async def crash(*args):
        raise ConnectionError("worker stopped after claim")

    monkeypatch.setattr(writer, "_publish_winner", crash)
    with pytest.raises(ConnectionError):
        await writer.publish(record)
    recovery = StreamRepository()
    assert await recovery.current(record.name) is None
    assert await recovery.recover_namespace(record.name, 0) == record
    assert await recovery.recover_namespace(record.name, 0) == record
    assert await recovery.current(record.name) == record
    with pytest.raises(StreamError):
        await recovery.publish(replace(record, stream_id=operation_id("losing retry")))
    assert await recovery.current(record.name) == record


async def test_upgrade_preserves_published_legacy_namespace_rows(source, engine_metadata):
    legacy = StreamRecord(
        StreamName(source, "legacy_stream"),
        0,
        operation_id("legacy identity"),
        StreamName(source, "target"),
        operation_id("legacy source"),
        "reader",
        ChangeCursor(2, 7),
    )
    async with engine_metadata.importer() as connection:
        await connection.execute(
            "INSERT INTO nova_meta.config_streams (catalog_name,database_name,schema_name,name,"
            "generation,stream_id,source_database,source_name,source_id,owner_role,cursor_epoch,"
            "cursor_sequence,status,created_at) VALUES ("
            + ",".join(["%s"] * 13)
            + ",clock_timestamp())",
            legacy.values,
        )
    repository = StreamRepository()
    assert await repository.current(legacy.name) == legacy
    dropped = replace(legacy, generation=1, status="DROPPED")
    assert await repository.publish(dropped) == dropped
    assert await repository.current(legacy.name) == dropped
