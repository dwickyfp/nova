"""Real-engine namespace persistence, isolated from the deployment's NOVA_SYSTEM."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.streams.claims import CLAIMS_DDL
from app.modules.streams.namespace import StreamName
from app.modules.streams.repository import STREAMS_DDL, StreamRecord, StreamRepository
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
        for ddl in (CLAIMS_DDL, STREAMS_DDL):
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
