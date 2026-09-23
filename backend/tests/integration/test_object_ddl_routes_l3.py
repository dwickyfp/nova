from __future__ import annotations

from uuid import uuid4

import asyncmy
import pytest

from tests.conftest import engine_host_ports


@pytest.mark.engine
async def test_table_and_view_ddl_routes_use_authenticated_connection(client, admin_token):
    conn = await asyncmy.connect(
        host="127.0.0.1", port=engine_host_ports()["starrocks-fe"], user="root", password=""
    )
    view = f"audit_view_{uuid4().hex}"
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE VIEW NOVA_SYSTEM.`{view}` AS SELECT 1 AS n")

        client.headers["Authorization"] = f"Bearer {admin_token}"
        table_response = await client.get(
            "/api/v1/tables/NOVA_SYSTEM/CONFIG_WORKSPACE_ENTRIES/ddl"
        )
        assert table_response.status_code == 200, table_response.text
        assert "CREATE TABLE" in table_response.json()["ddl"].upper()

        view_response = await client.get(f"/api/v1/views/NOVA_SYSTEM/{view}/ddl")
        assert view_response.status_code == 200, view_response.text
        assert "CREATE VIEW" in view_response.json()["ddl"].upper()
    finally:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP VIEW IF EXISTS NOVA_SYSTEM.`{view}`")
        conn.close()
