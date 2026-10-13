from contextlib import suppress
from uuid import uuid4

import asyncmy
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.core.config import settings
from app.core.security import encrypt_password
from app.modules.query.service import query_service
from tests.conftest import engine_host_ports

pytestmark = pytest.mark.engine


async def test_pipe_api_metadata_permissions_and_redaction(
    client, admin_token, analyst_token, sr_root, minio_client
):
    database = "pipe_api_" + uuid4().hex[:12]
    key = database + ".parquet"
    sink = pa.BufferOutputStream()
    pq.write_table(pa.table({"id": [1, 2], "amount": [10, 20]}), sink)
    minio_client.put_object(Bucket="test-stage", Key=key, Body=sink.getvalue().to_pybytes())
    sr_root = await asyncmy.connect(
        host="127.0.0.1", port=engine_host_ports()["starrocks-fe"], user="root", password=""
    )
    async with sr_root.cursor() as cur:
        await cur.execute(f"CREATE DATABASE `{database}`")
        await cur.execute(
            f"CREATE TABLE `{database}`.target (id INT, amount INT) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES('replication_num'='1')"
        )
        await cur.execute(f"GRANT ALL ON DATABASE `{database}` TO USER nova_admin")
        await cur.execute(f"GRANT ALL ON ALL TABLES IN DATABASE `{database}` TO USER nova_admin")
    port = engine_host_ports()["minio"]
    body = {
        "name": "synthetic_pipe",
        "database": database,
        "auto_ingest": False,
        "poll_interval": 3600,
        "sql": (
            f"INSERT INTO `{database}`.target SELECT * FROM FILES("
            f"'path'='s3://test-stage/{key}', 'format'='parquet',"
            f"'aws.s3.endpoint'='http://minio:{port}', 'aws.s3.enable_path_style_access'='true',"
            "'aws.s3.access_key'='minioadmin', 'aws.s3.secret_key'='minioadmin')"
        ),
    }
    try:
        client.headers["Authorization"] = "Bearer " + admin_token
        created = await client.post("/api/v1/pipes", json=body)
        assert created.status_code == 201, created.text
        assert created.json()["name"] == body["name"]
        assert created.json()["database"] == database
        assert "minioadmin" not in created.text
        listed = await client.get("/api/v1/pipes", params={"database": database})
        assert listed.status_code == 200, listed.text
        assert listed.json()["pipes"][0]["name"] == body["name"]
        url = "/api/v1/pipes/synthetic_pipe"
        detail = await client.get(url, params={"database": database})
        assert detail.status_code == 200, detail.text
        assert "INSERT INTO" in detail.json()["sql"]
        assert detail.json()["properties"]
        properties = {key.casefold(): value for key, value in detail.json()["properties"].items()}
        assert properties["poll_interval"] == "3600"
        assert "minioadmin" not in detail.text
        files = await client.get(url + "/files", params={"database": database})
        assert files.status_code == 200, files.text
        suspended = await client.patch(url + "/suspend", params={"database": database})
        assert suspended.status_code == 200, suspended.text
        resumed = await client.patch(url + "/resume", params={"database": database})
        assert resumed.status_code == 200, resumed.text

        client.headers["Authorization"] = "Bearer " + analyst_token
        refused = await client.post("/api/v1/pipes", json={**body, "name": "forbidden_pipe"})
        assert refused.status_code in {400, 403}, refused.text
        refused = await client.delete(url, params={"database": database})
        assert refused.status_code in {400, 403}, refused.text
        assert "minioadmin" not in refused.text
        client.headers["Authorization"] = "Bearer " + admin_token
        assert (await client.get(url, params={"database": database})).status_code == 200
        deleted = await client.delete(url, params={"database": database})
        assert deleted.status_code == 200, deleted.text
        assert (await client.get(url, params={"database": database})).status_code == 404
        async with sr_root.cursor() as cur:
            await cur.execute(
                "SELECT sql_text, rewritten_sql FROM NOVA_SYSTEM.AUDIT_LOG "
                "WHERE user_name='nova_admin' AND sql_text LIKE %s",
                (f"%{database}%",),
            )
            audits = await cur.fetchall()
            assert audits and "minioadmin" not in str(audits)
    finally:
        async with sr_root.cursor() as cur:
            with suppress(Exception):
                await cur.execute(f"DROP PIPE IF EXISTS `{database}`.synthetic_pipe")
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        minio_client.delete_object(Bucket="test-stage", Key=key)
        sr_root.close()


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE PIPE nonexistent.p AS INSERT INTO nonexistent.t SELECT * FROM "
        "FILES('path'='s3://test/x.parquet','format'='parquet')",
        "ALTER PIPE nonexistent.p RESUME",
        "ALTER PIPE nonexistent.p RETRY ALL",
    ],
)
async def test_ranger_pipe_gate_precedes_engine_execution(app, monkeypatch, sql):
    monkeypatch.setattr(settings, "RANGER_ENABLED", True)
    result = await query_service.execute(
        sql=sql, username="testanalyst", encrypted_password=encrypt_password("testpass"),
        role="test_analyst", security_context_version=3,
    )
    assert "Pipe execution is unavailable with Ranger" in result.error
    assert result.engine_query_ids == []
