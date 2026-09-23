"""Run Nova stage command lowering against StarRocks 4.1 and test storage."""

from __future__ import annotations

from contextlib import suppress
from uuid import uuid4

import asyncmy
import boto3
import pytest

from app.modules.query.dialect.parser import parse_sql
from app.modules.query.dialect.translator import StorageConfig, translate_stage_query
from app.modules.stages.access import StageAccessDenied, check_stage_access
from tests.conftest import engine_host_ports, require_stack

pytestmark = pytest.mark.engine


async def test_stage_list_load_and_export_on_engine(docker_services) -> None:
    require_stack(docker_services)
    ports = engine_host_ports()
    sr_root = await asyncmy.connect(
        host="127.0.0.1", port=ports["starrocks-fe"], user="root", password=""
    )
    minio_client = boto3.client(
        "s3", endpoint_url=f"http://127.0.0.1:{ports['minio']}",
        aws_access_key_id="minioadmin", aws_secret_access_key="minioadmin",
    )
    with suppress(minio_client.exceptions.BucketAlreadyOwnedByYou):
        minio_client.create_bucket(Bucket="test-stage")
    suffix = uuid4().hex[:12]
    database = f"nova_stage_audit_{suffix}"
    prefix = f"stage-audit/{suffix}"
    bucket = "test-stage"
    minio_client.put_object(
        Bucket=bucket, Key=f"{prefix}/input.csv", Body=b"1,alice\n2,bob\n"
    )
    config = StorageConfig(
        storage_type="s3", endpoint=f"http://minio:{ports['minio']}", bucket=bucket,
        base_prefix=prefix, access_key="minioadmin", secret_key="minioadmin",
    )

    def lower(sql: str) -> str:
        return translate_stage_query(
            parse_sql(sql), {"stage": config}, files_params={"csv.column_separator": ","}
        )[0]

    try:
        async with sr_root.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{database}`")
            await cur.execute(
                f"CREATE TABLE `{database}`.`loaded` (id INT, name VARCHAR(32)) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
            await cur.execute(lower("LIST @stage/"))
            listed = await cur.fetchall()
            assert listed
            await cur.execute(lower(f"COPY INTO `{database}`.`loaded` FROM @stage.input.csv"))
            await cur.execute(f"SELECT COUNT(*) FROM `{database}`.`loaded`")
            assert (await cur.fetchone())[0] == 2
            await cur.execute(
                lower(f"COPY INTO @stage.copy.parquet FROM `{database}`.`loaded`")
            )
            await cur.execute(
                lower(f"INSERT INTO @stage.insert.parquet SELECT * FROM `{database}`.`loaded`")
            )
        objects = minio_client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
        keys = [item["Key"] for item in objects]
        assert any("copy.parquet" in key for key in keys)
        assert any("insert.parquet" in key for key in keys)
    finally:
        async with sr_root.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        objects = minio_client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
        for item in objects:
            minio_client.delete_object(Bucket=bucket, Key=item["Key"])
        sr_root.close()


async def test_stage_access_uses_real_active_role_grants(docker_services, monkeypatch) -> None:
    import app.modules.stages.access as access_module

    require_stack(docker_services)
    port = engine_host_ports()["starrocks-fe"]
    suffix = uuid4().hex[:10]
    database = f"nova_stage_grants_{suffix}"
    role = f"nova_stage_role_{suffix}"
    user = f"nova_stage_user_{suffix}"
    root = await asyncmy.connect(host="127.0.0.1", port=port, user="root", password="")
    member = None
    try:
        async with root.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{database}`")
            await cur.execute(f"CREATE ROLE `{role}`")
            await cur.execute(f"CREATE USER '{user}' IDENTIFIED BY 'test-only-password'")
            await cur.execute(f"GRANT `{role}` TO '{user}'")
            await cur.execute(
                f"GRANT SELECT ON ALL TABLES IN DATABASE `{database}` TO ROLE `{role}`"
            )

        async def role_grants(sql):
            async with root.cursor() as cur:
                await cur.execute(sql)
                return {"rows": list(await cur.fetchall())}

        monkeypatch.setattr(access_module.db, "execute_system", role_grants)
        monkeypatch.setattr(access_module.settings, "RANGER_ENABLED", False)
        member = await asyncmy.connect(
            host="127.0.0.1", port=port, user=user, password="test-only-password"
        )
        stage = {"database_name": database, "schema_name": "bronze"}
        await check_stage_access(
            stage, action="read", username=user, active_role=role, connection=member
        )
        with pytest.raises(StageAccessDenied):
            await check_stage_access(
                stage, action="write", username=user, active_role=role, connection=member
            )
    finally:
        if member is not None:
            member.close()
        async with root.cursor() as cur:
            await cur.execute(f"DROP USER IF EXISTS '{user}'")
            await cur.execute(f"DROP ROLE IF EXISTS `{role}`")
            await cur.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()
