from __future__ import annotations

import os
import secrets
from contextlib import suppress
from io import BytesIO
from uuid import uuid4

import asyncmy
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.modules.agents.registry import add_custom_tools, build_registry
from app.modules.agents.repository import agent_repository
from app.modules.agents.tools.custom_tool import CustomToolRunner
from app.modules.assistant.service import LoopContext
from app.modules.assistant.tools import ToolInvocation
from tests.conftest import engine_host_ports

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_CUSTOM_SQL_LIVE") != "1",
        reason="Set NOVA_CUSTOM_SQL_LIVE=1 for the isolated custom-tool engine test",
    ),
]


def _runner(name: str, database: str, sql: str, *, parameter: bool = True) -> CustomToolRunner:
    return CustomToolRunner(
        {
            "name": name,
            "kind": "procedure",
            "description": name,
            "database_name": database,
            "definition": {
                "parameters": (
                    [{"name": "value", "type": "string", "description": "Row value"}]
                    if parameter
                    else []
                ),
                "statements": [sql],
                "output_mode": "result",
            },
        }
    )


async def test_custom_sql_tools_insert_select_update_delete_on_scoped_engine_user(
    sr_root, minio_client, monkeypatch,
) -> None:
    import app.core.config as cfg
    from app.common.nova_system import init_nova_system
    from app.core.config import get_storage_connection
    from app.core.database import db
    from app.core.security import encrypt_password
    from app.modules.query.service import query_service
    from app.modules.stages.service import stage_service

    ports = engine_host_ports()
    monkeypatch.setattr(cfg.settings, "STARROCKS_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg.settings, "STARROCKS_FE_MYSQL_PORT", ports["starrocks-fe"])
    monkeypatch.setattr(cfg.settings, "STARROCKS_ROOT_USER", "root")
    monkeypatch.setattr(cfg.settings, "STARROCKS_ROOT_PASSWORD", "")
    monkeypatch.setattr(cfg.settings, "S3_ENDPOINT", f"http://127.0.0.1:{ports['minio']}")
    monkeypatch.setattr(cfg.settings, "S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setattr(cfg.settings, "S3_SECRET_KEY", "minioadmin")
    monkeypatch.setattr(cfg.settings, "RANGER_ENABLED", False)
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    cfg.load_nova_app_config.cache_clear()
    await db.init_system_pool()
    await init_nova_system()
    await agent_repository.ensure_schema()

    suffix = uuid4().hex[:10]
    database = f"nova_custom_{suffix}"
    role = f"custom_role_{suffix}"
    username = f"custom_user_{suffix}"
    password = secrets.token_urlsafe(20)
    created_database = False
    created_role = False
    created_user = False
    stage_id = None
    bucket = None
    object_key = None
    created_bucket = False
    agent_id = None
    custom_tool_ids: list[str] = []
    root = await asyncmy.connect(
        host="127.0.0.1", port=ports["starrocks-fe"], user="root", password=""
    )

    try:
        async with root.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{database}`")
            created_database = True
            await cursor.execute(
                f"CREATE TABLE `{database}`.`records` "
                "(id BIGINT NOT NULL, value VARCHAR(64)) "
                "PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                'PROPERTIES("replication_num"="1")'
            )
            await cursor.execute(f"CREATE ROLE `{role}`")
            created_role = True
            await cursor.execute(f"CREATE USER '{username}' IDENTIFIED BY '{password}'")
            created_user = True
            await cursor.execute(f"GRANT `{role}` TO '{username}'")
            await cursor.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN DATABASE `{database}` TO ROLE `{role}`"
            )

        context = LoopContext(
            user_name=username,
            database=database,
            thread_id=f"custom-live-{suffix}",
            user={
                "username": username,
                "encrypted_password": encrypt_password(password),
                "active_role": role,
                "assigned_roles": [role],
                "security_context_version": 1,
            },
        )

        pipeline_errors: list[str] = []
        original_execute = query_service.execute_statements

        async def capture_errors(**kwargs):
            results = await original_execute(**kwargs)
            pipeline_errors.extend(
                str(result.error).replace(password, "***")
                for result in results
                if result.error
            )
            return results

        monkeypatch.setattr(query_service, "execute_statements", capture_errors)

        async def run(name: str, sql: str, value: str | None = None):
            tool = _runner(name, database, sql, parameter=value is not None)
            args = {"value": value} if value is not None else {}
            outcome = await tool.run(ToolInvocation(name, tool.name, args), context)
            assert outcome.ok, f"{name}: {outcome.error}; pipeline={pipeline_errors[-1:]}"
            return outcome

        await run("insert_record", "INSERT INTO records (id, value) VALUES (1, {{value}})", "one")
        selected = await run("select_record", "SELECT value FROM records WHERE id = 1")
        assert selected.table is not None
        assert selected.table["rows"] == [["one"]]

        await run("update_record", "UPDATE records SET value = {{value}} WHERE id = 1", "two")
        selected = await run("select_updated", "SELECT value FROM records WHERE id = 1")
        assert selected.table is not None
        assert selected.table["rows"] == [["two"]]

        await run("delete_record", "DELETE FROM records WHERE id = 1")
        selected = await run("select_deleted", "SELECT value FROM records WHERE id = 1")
        assert selected.table is not None
        assert selected.table["rows"] == []

        storage = get_storage_connection("production")
        bucket = storage.bucket
        existing_buckets = {item["Name"] for item in minio_client.list_buckets()["Buckets"]}
        if bucket not in existing_buckets:
            minio_client.create_bucket(Bucket=bucket)
            created_bucket = True
        prefix = f"custom-sql-tools/{suffix}"
        object_key = f"{prefix}/data.parquet"
        payload = BytesIO()
        pq.write_table(pa.table({"id": [1], "value": ["from_stage"]}), payload)
        minio_client.put_object(Bucket=bucket, Key=object_key, Body=payload.getvalue())
        stage_name = f"stage_{suffix}"
        stage = await stage_service.create_stage(
            {
                "name": stage_name,
                "database_name": database,
                "schema_name": "default",
                "storage_connection": "production",
                "base_prefix": prefix,
            },
            username,
        )
        assert stage is not None
        stage_id = stage["id"]
        context.schema_name = "default"
        selected = await run(
            "select_stage", f"SELECT * FROM @{stage_name}.data.parquet"
        )
        assert selected.table is not None
        assert selected.table["rows"] == [[1, "from_stage"]]

        await run(
            "insert_for_export",
            "INSERT INTO records (id, value) VALUES (2, {{value}})",
            "exported",
        )
        await run(
            "export_stage",
            f"COPY INTO @{stage_name}.export.parquet FROM records",
        )
        exported = minio_client.list_objects_v2(
            Bucket=bucket, Prefix=f"{prefix}/export.parquet"
        ).get("Contents", [])
        assert exported

        for name, statement, output_mode in (
            (
                "insert_record",
                "INSERT INTO records (id, value) VALUES (3, {{value}})",
                "run",
            ),
            (
                "select_record",
                "SELECT value FROM records WHERE id = 3 AND value = {{value}}",
                "result",
            ),
        ):
            created_tool = await agent_repository.create_custom_tool(
                owner_name=username,
                fields={
                    "name": f"{name}_{suffix}",
                    "description": f"Temporary {name} validation tool",
                    "kind": "procedure",
                    "database_name": database,
                    "definition": {
                        "parameters": [
                            {"name": "value", "type": "string", "description": "Row value"}
                        ],
                        "statements": [statement],
                        "output_mode": output_mode,
                    },
                },
            )
            custom_tool_ids.append(created_tool["tool_id"])
        selected_names = [
            f"custom:insert_record_{suffix}", f"custom:select_record_{suffix}"
        ]
        created_agent = await agent_repository.create_agent(
            owner_name=username,
            fields={
                "name": f"Custom SQL validation {suffix}",
                "description": "Temporary, isolated custom SQL tool test agent",
                "database_name": database,
                "default_tools": selected_names,
            },
        )
        agent_id = created_agent["agent_id"]
        persisted_agent = await agent_repository.get_agent(agent_id, owner_name=username)
        assert persisted_agent is not None
        registry = build_registry(persisted_agent)
        await add_custom_tools(registry, persisted_agent)
        assert registry.names() == [
            f"custom_insert_record_{suffix}", f"custom_select_record_{suffix}"
        ]
        insert_tool = registry.get(f"custom_insert_record_{suffix}")
        select_tool = registry.get(f"custom_select_record_{suffix}")
        assert insert_tool is not None and select_tool is not None
        inserted = await insert_tool.run(
            ToolInvocation("agent-insert", insert_tool.name, {"value": "persisted"}),
            context,
        )
        selected = await select_tool.run(
            ToolInvocation("agent-select", select_tool.name, {"value": "persisted"}),
            context,
        )
        assert inserted.ok and selected.ok, pipeline_errors[-1:]
        assert selected.table is not None
        assert selected.table["rows"] == [["persisted"]]

        stage_sql = os.getenv("NOVA_CUSTOM_SQL_LIVE_STAGE_SQL", "").strip()
        stage_database = os.getenv("NOVA_CUSTOM_SQL_LIVE_STAGE_DATABASE", "").strip()
        if stage_sql and stage_database:
            assert stage_sql.upper().startswith(("SELECT ", "LIST "))
            async with root.cursor() as cursor:
                await cursor.execute(
                    f"GRANT SELECT ON ALL TABLES IN DATABASE `{stage_database}` "
                    f"TO ROLE `{role}`"
                )
            stage_tool = _runner("stage_read", stage_database, stage_sql, parameter=False)
            stage_result = await stage_tool.run(
                ToolInvocation("stage", stage_tool.name, {}), context
            )
            assert stage_result.ok, stage_result.error
    finally:
        if agent_id is not None:
            with suppress(Exception):
                await agent_repository.delete_agent(agent_id, owner_name=username)
        for tool_id in custom_tool_ids:
            with suppress(Exception):
                await agent_repository.delete_custom_tool(tool_id, owner_name=username)
        if stage_id is not None:
            with suppress(Exception):
                await stage_service.delete_stage(stage_id)
        if bucket and object_key:
            with suppress(Exception):
                objects = minio_client.list_objects_v2(Bucket=bucket, Prefix=prefix).get(
                    "Contents", []
                )
                for item in objects:
                    minio_client.delete_object(Bucket=bucket, Key=item["Key"])
        if created_bucket and bucket:
            with suppress(Exception):
                minio_client.delete_bucket(Bucket=bucket)
        async with root.cursor() as cursor:
            if created_user:
                with suppress(Exception):
                    await cursor.execute(f"DROP USER IF EXISTS '{username}'")
            if created_role:
                with suppress(Exception):
                    await cursor.execute(f"DROP ROLE IF EXISTS `{role}`")
            if created_database:
                with suppress(Exception):
                    await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
        root.close()
        await db.close_system_pool()
        cfg.load_nova_app_config.cache_clear()
