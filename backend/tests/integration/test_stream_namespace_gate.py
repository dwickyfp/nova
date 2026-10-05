"""Real-engine namespace persistence, isolated from the deployment's NOVA_SYSTEM."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.streams.claims import CLAIMS_DDL
from app.modules.streams.namespace import StreamName
from app.modules.streams.repository import (
    NAMESPACE_OPERATIONS_DDL,
    STREAMS_DDL,
    StreamRecord,
    StreamRepository,
)
from app.modules.streams.schemas import ChangeCursor, StreamError, operation_id
from tests.integration import test_stream_durability_gate as gate

connect = gate.connect
source = gate.source

pytestmark = pytest.mark.engine


async def executor(database, sql, params=()):
    conn = await connect()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(sql.replace("NOVA_SYSTEM.", f"`{database}`."), params)
            rows = await cursor.fetchall() if cursor.description else ()
            return {"rows": list(rows), "affected": cursor.rowcount}
    finally:
        conn.close()


async def test_fresh_upgrade_repeat_and_concurrent_namespace_claim(source):
    async def execute(sql, params=()):
        return await executor(source, sql, params)

    migration = Path(__file__).parents[2] / "migrations/20261005_stream_namespace.sql"
    for _ in range(2):
        for sql in migration.read_text().split(";"):
            if sql.strip():
                await execute(sql)
        recovery = migration.with_name("20261005_stream_namespace_recovery.sql")
        for sql in recovery.read_text().split(";"):
            if sql.strip():
                await execute(sql)
        for ddl in (CLAIMS_DDL, STREAMS_DDL, NAMESPACE_OPERATIONS_DDL):
            await execute(ddl)

    name = StreamName(source, "orders_stream")
    record = StreamRecord(
        name, 0, operation_id("first"), StreamName(source, "target"),
        operation_id("source"), "reader", ChangeCursor(3, 42),
    )
    repositories = [StreamRepository(execute), StreamRepository(execute)]
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
    async def execute(sql, params=()):
        return await executor(source, sql, params)

    for ddl in (CLAIMS_DDL, STREAMS_DDL, NAMESPACE_OPERATIONS_DDL):
        await execute(ddl)
    record = StreamRecord(
        StreamName(source, "crash_stream"), 0, operation_id("winner"),
        StreamName(source, "target"), operation_id("source"), "reader", ChangeCursor(1, 0),
    )
    writer = StreamRepository(execute)

    async def crash(*args):
        raise ConnectionError("worker stopped after claim")

    monkeypatch.setattr(writer, "_publish_winner", crash)
    with pytest.raises(ConnectionError):
        await writer.publish(record)
    recovery = StreamRepository(execute)
    assert await recovery.current(record.name) is None
    assert await recovery.recover_namespace(record.name, 0) == record
    assert await recovery.recover_namespace(record.name, 0) == record
    assert await recovery.current(record.name) == record
    with pytest.raises(StreamError):
        await recovery.publish(replace(record, stream_id=operation_id("losing retry")))
    assert await recovery.current(record.name) == record


async def test_upgrade_preserves_published_legacy_namespace_rows(source):
    async def execute(sql, params=()):
        return await executor(source, sql, params)

    migration = Path(__file__).parents[2] / "migrations/20261005_stream_namespace.sql"
    for sql in migration.read_text().split(";"):
        if sql.strip():
            await execute(sql)
    legacy = StreamRecord(
        StreamName(source, "legacy_stream"), 0, operation_id("legacy identity"),
        StreamName(source, "target"), operation_id("legacy source"), "reader", ChangeCursor(2, 7),
    )
    await execute(
        "INSERT INTO NOVA_SYSTEM.CONFIG_STREAMS VALUES ("
        + ",".join(["%s"] * 13) + ",NOW())", legacy.values,
    )
    for _ in range(2):
        recovery = migration.with_name("20261005_stream_namespace_recovery.sql")
        for sql in recovery.read_text().split(";"):
            if sql.strip():
                await execute(sql)
    repository = StreamRepository(execute)
    assert await repository.current(legacy.name) == legacy
    dropped = replace(legacy, generation=1, status="DROPPED")
    assert await repository.publish(dropped) == dropped
    assert await repository.current(legacy.name) == dropped
