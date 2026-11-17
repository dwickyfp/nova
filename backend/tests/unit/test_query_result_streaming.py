import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncmy.cursors
import pytest

from app.modules.query.repository import QueryRepository, QueryResult, _stream_result


class Cursor:
    description = [("value", 3, None, None, None, None, True)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, sql):
        self.sql = sql

    async def fetchmany(self, size):
        assert size == 3
        return [(1,), (2,), (3,)]

    async def fetchall(self):
        raise AssertionError("bounded result must not fetch all rows")


class Connection:
    def __init__(self):
        self.cursor_type = None

    def cursor(self, cursor_type):
        self.cursor_type = cursor_type
        return Cursor()


@pytest.mark.asyncio
async def test_bounded_user_query_uses_unbuffered_cursor():
    conn = Connection()

    result = await QueryRepository._execute_on(
        conn, "SELECT value FROM large_table", role=None, max_rows=2, start=0.0
    )

    assert conn.cursor_type is asyncmy.cursors.SSDictCursor
    assert result.rows == [[1], [2]]
    assert result.truncated is True
    assert result.row_count == 2


@pytest.mark.parametrize("failure", [ValueError("byte budget"), asyncio.CancelledError()])
async def test_stream_failure_closes_connection_before_cursor_cleanup(failure):
    conn = SimpleNamespace(close=Mock())
    result_set = SimpleNamespace(unbuffered_active=True, has_next=True, connection=conn)
    cursor = SimpleNamespace(
        fetchmany=AsyncMock(return_value=[(1,)]), _result=result_set, connection=conn
    )
    sink = SimpleNamespace(
        fetch_batch_rows=1, begin=AsyncMock(), rows=AsyncMock(side_effect=failure)
    )
    with pytest.raises(type(failure)):
        await _stream_result(cursor, conn, sink, QueryResult(columns=["value"]), False)
    conn.close.assert_called_once()
    cursor.fetchmany.assert_awaited_once_with(1)
    assert not result_set.unbuffered_active and not result_set.has_next
    assert cursor.connection is None and result_set.connection is None


async def test_existing_stream_sinks_keep_the_default_batch_and_empty_result():
    conn = SimpleNamespace(close=Mock())
    cursor = SimpleNamespace(fetchmany=AsyncMock(return_value=[]))
    sink = SimpleNamespace(begin=AsyncMock(), rows=AsyncMock())
    result = QueryResult(columns=["value"])
    await _stream_result(cursor, conn, sink, result, False)
    cursor.fetchmany.assert_awaited_once_with(1000)
    assert result.streamed and result.row_count == 0
    conn.close.assert_not_called()


@pytest.mark.parametrize("count", [0, 2, 3])
async def test_bound_stream_enforces_the_limit_even_if_the_sink_accepts_more(count):
    from app.core.exceptions import StarRocksError

    conn = SimpleNamespace(close=Mock())
    result_set = SimpleNamespace(unbuffered_active=True, has_next=True, connection=conn)
    cursor = SimpleNamespace(
        fetchmany=AsyncMock(side_effect=[[(n,) for n in range(count)], []]),
        _result=result_set, connection=conn,
    )
    sink = SimpleNamespace(begin=AsyncMock(), rows=AsyncMock())
    result = QueryResult(columns=["value"])
    pending = _stream_result(cursor, conn, sink, result, False, row_limit=2)
    if count > 2:
        with pytest.raises(StarRocksError, match="streaming result limit"):
            await pending
        sink.rows.assert_not_awaited()
        conn.close.assert_called_once()
        assert not result.streamed and cursor.connection is None
        assert not result_set.unbuffered_active and result_set.connection is None
    else:
        await pending
        assert result.streamed and result.row_count == count and result.rows == []
        conn.close.assert_not_called()
        if count:
            sink.rows.assert_awaited_once_with([[0], [1]])
            assert cursor.fetchmany.await_args.args == (1,)
    assert cursor.fetchmany.await_args_list[0].args == (3,)


async def test_bound_parameters_use_the_existing_unbuffered_sink_without_losing_exact_values():
    from decimal import Decimal

    from app.sql_frontend.parameters import BoundParameters, ParameterSpec

    exact = Decimal("12345678901234567890123456.123456789012")
    calls = []

    class BoundCursor(Cursor):
        def __init__(self):
            self.read = False

        async def execute(self, sql, args=None):
            calls.append((sql, args))

        async def fetchmany(self, size):
            assert size == 1
            if self.read:
                return []
            self.read = True
            return [(exact,)]

    class BoundConnection(Connection):
        def cursor(self, cursor_type):
            self.cursor_type = cursor_type
            return BoundCursor()

    conn = BoundConnection()
    sink = SimpleNamespace(fetch_batch_rows=1, begin=AsyncMock(), rows=AsyncMock())
    bindings = BoundParameters(
        (ParameterSpec("amount", "decimal", precision=38, scale=12),), (("amount", exact),),
    )
    result = await QueryRepository._execute_on(
        conn, "SELECT :amount AS value", role="analyst", max_rows=1,
        start=0, row_sink=sink, parameters=bindings,
    )
    assert conn.cursor_type is asyncmy.cursors.SSCursor
    assert calls == [("SET ROLE analyst", None), ("SET enable_profile = false", None),
                     ("SELECT CAST(%s AS DECIMAL(38,12)) AS value", (format(exact, "f"),))]
    sink.rows.assert_awaited_once_with([[exact]])
    assert result.streamed and result.row_count == 1 and result.rows == []
