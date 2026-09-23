import time

import asyncmy
import pytest

from app.modules.query.repository import QueryRepository
from tests.conftest import engine_host_ports, require_stack


@pytest.mark.engine
async def test_bounded_query_leaves_starrocks_connection_reusable(docker_services):
    require_stack(docker_services)
    conn = await asyncmy.connect(
        host="127.0.0.1", port=engine_host_ports()["starrocks-fe"], user="root", password=""
    )
    try:
        result = await QueryRepository._execute_on(
            conn,
            "SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3",
            role=None,
            max_rows=2,
            start=time.monotonic(),
        )

        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 42")
            row = await cursor.fetchone()
    finally:
        conn.close()

    assert result.rows == [[1], [2]]
    assert row == (42,)
