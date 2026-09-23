import asyncmy
import pytest

from tests.conftest import engine_host_ports, require_stack

pytestmark = pytest.mark.engine


async def test_operational_tables_keep_dynamic_partitions(docker_services):
    require_stack(docker_services)
    conn = await asyncmy.connect(
        host="127.0.0.1", port=engine_host_ports()["starrocks-fe"], user="root", password=""
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute("SHOW DYNAMIC PARTITION TABLES FROM NOVA_SYSTEM")
            rows = await cur.fetchall()
    finally:
        conn.close()

    by_name = {str(row[0]): row for row in rows}
    for name in ("AUDIT_LOG", "LINEAGE_LOAD_HISTORY", "USAGE_QUERY_STATS"):
        row = by_name[name]
        assert str(row[1]).lower() == "true"
        assert row[2] == "MONTH"
        assert int(row[4]) >= 6
        assert row[11] == "NORMAL"
