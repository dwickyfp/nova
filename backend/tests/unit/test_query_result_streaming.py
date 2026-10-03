import asyncmy.cursors
import pytest

from app.modules.query.repository import QueryRepository


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
