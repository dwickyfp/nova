from uuid import uuid4

import asyncmy
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.core.security import encrypt_password
from app.modules.query.service import query_service
from tests.conftest import engine_host_ports, root_password, s3_credentials

pytestmark = pytest.mark.engine


async def test_pipe_api_refuses_storage_urls_and_credentials_and_creates_no_engine_pipe(
    client, admin_token, sr_root, minio_client, metadata_inspect
):
    """A pipe reads a registered stage: a raw URL with keys is refused before any I/O."""
    database = "pipe_api_" + uuid4().hex[:12]
    key = database + ".parquet"
    sink = pa.BufferOutputStream()
    pq.write_table(pa.table({"id": [1, 2], "amount": [10, 20]}), sink)
    minio_client.put_object(Bucket="test-stage", Key=key, Body=sink.getvalue().to_pybytes())
    sr_root = await asyncmy.connect(
        host="127.0.0.1",
        port=engine_host_ports()["starrocks-fe"],
        user="root",
        password=root_password(),
    )
    async with sr_root.cursor() as cur:
        await cur.execute(f"CREATE DATABASE `{database}`")
        await cur.execute(
            f"CREATE TABLE `{database}`.target (id INT, amount INT) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            "PROPERTIES('replication_num'='1')"
        )
    port = engine_host_ports()["minio"]
    access_key, secret_key = s3_credentials()
    body = {
        "name": "synthetic_pipe",
        "database": database,
        "auto_ingest": False,
        "poll_interval": 3600,
        "sql": (
            f"INSERT INTO `{database}`.target SELECT * FROM FILES("
            f"'path'='s3://test-stage/{key}', 'format'='parquet',"
            f"'aws.s3.endpoint'='http://minio:{port}', 'aws.s3.enable_path_style_access'='true',"
            f"'aws.s3.access_key'='{access_key}', 'aws.s3.secret_key'='{secret_key}')"
        ),
    }
    try:
        client.headers["Authorization"] = "Bearer " + admin_token
        created = await client.post("/api/v1/pipes", json=body)
        assert created.status_code == 400, created.text
        assert "A pipe reads files from a stage" in created.json()["detail"]
        assert secret_key not in created.text
        absent = await client.get("/api/v1/pipes", params={"database": database})
        assert absent.status_code == 200 and absent.json()["pipes"] == []
        async with sr_root.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM information_schema.pipes WHERE DATABASE_NAME = %s",
                (database,),
            )
            assert (await cur.fetchone())[0] == 0
        missing = await client.patch("/api/v1/pipes/synthetic_pipe/resume",
                                     params={"database": database})
        assert missing.status_code == 400 and "does not exist" in missing.json()["detail"]
        audits = await metadata_inspect(
            "SELECT sql_text, rewritten_sql FROM nova_meta.audit_log "
            "WHERE user_name='nova_admin' AND sql_text LIKE %s",
            (f"%{database}%",),
        )
        assert secret_key not in str(audits)
    finally:
        async with sr_root.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        minio_client.delete_object(Bucket="test-stage", Key=key)
        sr_root.close()


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("CREATE PIPE nonexistent.p AS INSERT INTO nonexistent.t SELECT * FROM "
         "FILES('path'='s3://test/x.parquet','format'='parquet')",
         "A pipe reads files from a stage"),
        ("ALTER PIPE nonexistent.p RESUME", "does not exist"),
        ("ALTER PIPE nonexistent.p RETRY ALL", "does not exist"),
    ],
)
async def test_pipe_statements_never_reach_the_engine(app, monkeypatch, sql, message):
    result = await query_service.execute(
        sql=sql,
        username="testanalyst",
        encrypted_password=encrypt_password("testpass"),
        role="test_analyst",
        security_context_version=3,
    )
    assert message in result.error
    assert result.engine_query_ids == []
