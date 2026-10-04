"""Cancelled metadata responses cannot contaminate the next pooled query."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app.core.database import StarRocksConnectionFactory


class Connection:
    def __init__(self, phase):
        self.phase = phase
        self.closed = False
        self.entered = asyncio.Event()
        self.pending_response = None
        self.cleanup_saw_closed = False
        self._connected = True

    def close(self):
        self.closed = True

    def cursor(self, _kind):
        return Cursor(self)


class Cursor:
    description = [("value",)]

    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, _value, _trace):
        if kind is asyncio.CancelledError:
            self.connection.cleanup_saw_closed = self.connection.closed

    async def execute(self, sql, _params):
        if sql == "delayed":
            self.connection.pending_response = 41
            if self.connection.phase == "execute":
                self.connection.entered.set()
                await asyncio.Event().wait()
            self.rows = [{"value": 41}]
        else:
            value = self.connection.pending_response or 99
            self.rows = [{"value": value}]

    async def fetchall(self):
        if self.connection.phase == "fetch" and self.connection.pending_response:
            self.connection.entered.set()
            await asyncio.Event().wait()
        return self.rows


class Pool:
    def __init__(self, phase):
        self.connections = [Connection(phase)]
        self.cond = asyncio.Condition()
        self.borrowed = False

    @asynccontextmanager
    async def acquire(self):
        async with self.cond:
            while self.borrowed:
                await self.cond.wait()
            if not self.connections[-1]._connected:
                self.connections.append(Connection("normal"))
            self.borrowed = True
        try:
            yield self.connections[-1]
        finally:
            self.borrowed = False
            if self.connections[-1]._connected:
                async with self.cond:
                    self.cond.notify()


@pytest.mark.parametrize("phase", ["execute", "fetch"])
async def test_cancelled_query_discards_response_before_cursor_cleanup(phase):
    pool = Pool(phase)
    factory = StarRocksConnectionFactory()
    factory._system_pool = pool
    first = pool.connections[0]
    pending = asyncio.create_task(factory.execute_system("delayed"))
    await asyncio.wait_for(first.entered.wait(), timeout=5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert first.closed and first.cleanup_saw_closed
    result = await factory.execute_system("next")
    assert result["rows"] == [[99]]
    assert len(pool.connections) == 2


@pytest.mark.parametrize("error", [asyncio.CancelledError, TimeoutError])
async def test_cancelled_direct_borrow_is_not_reused(error):
    pool = Pool("normal")
    factory = StarRocksConnectionFactory()
    factory._system_pool = pool
    with pytest.raises(error):
        async with factory.system_conn():
            raise error()
    assert pool.connections[0].closed
    assert (await factory.execute_system("next"))["rows"] == [[99]]


async def test_consumed_application_error_does_not_discard_valid_connection():
    pool = Pool("normal")
    factory = StarRocksConnectionFactory()
    factory._system_pool = pool
    with pytest.raises(ValueError):
        async with factory.system_conn():
            raise ValueError("Application rejected the consumed response")
    assert (await factory.execute_system("next"))["rows"] == [[99]]
    assert len(pool.connections) == 1 and not pool.connections[0].closed


async def test_waiting_borrower_wakes_after_cancelled_connection_is_discarded():
    pool = Pool("execute")
    factory = StarRocksConnectionFactory()
    factory._system_pool = pool
    pending = asyncio.create_task(factory.execute_system("delayed"))
    await asyncio.wait_for(pool.connections[0].entered.wait(), timeout=5)
    following = asyncio.create_task(factory.execute_system("next"))
    await asyncio.sleep(0)
    assert not following.done()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (await asyncio.wait_for(following, timeout=5))["rows"] == [[99]]
