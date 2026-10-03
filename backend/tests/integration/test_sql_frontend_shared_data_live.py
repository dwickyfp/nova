import os
from contextlib import asynccontextmanager, suppress
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncmy
import boto3
import pytest

from app.modules.query.repository import QueryRepository
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.starrocks import StarRocksCatalogProvider
from app.sql_frontend.capabilities.starrocks import StarRocksCapabilityProvider
from app.sql_frontend.context import ExecutionContext, PlanningContext
from app.sql_frontend.execution.executor import SQLExecutor
from app.sql_frontend.execution.transactions import TransactionRunner
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import Atomicity, CompositePlan
from app.sql_frontend.planning.planner import SQLPlanner

pytestmark = pytest.mark.engine


@pytest.fixture
async def shared_data():
    port = os.getenv("NOVA_SHARED_DATA_PORT")
    if not port:
        pytest.skip("Start docker-compose.shared-data.test.yml and set NOVA_SHARED_DATA_PORT")
    root = await asyncmy.connect(host="127.0.0.1", port=int(port), user="root", autocommit=True)
    name = "hardening_" + uuid4().hex[:12]
    password = "hardening-test-only"
    try:
        client = boto3.client(
            "s3",
            endpoint_url="http://127.0.0.1:" + os.getenv("NOVA_SHARED_DATA_STORAGE_PORT", "49000"),
            aws_access_key_id="hardening",
            aws_secret_access_key=password,
        )
        with suppress(client.exceptions.BucketAlreadyOwnedByYou):
            client.create_bucket(Bucket="hardening")
        async with root.cursor() as cursor:
            await cursor.execute(
                "CREATE STORAGE VOLUME IF NOT EXISTS hardening TYPE=S3 "
                "LOCATIONS=('s3://hardening/') PROPERTIES('aws.s3.endpoint'='http://minio:9000',"
                "'aws.s3.access_key'='hardening','aws.s3.secret_key'='hardening-test-only',"
                "'aws.s3.use_instance_profile'='false','aws.s3.use_aws_sdk_default_behavior'='false',"
                "'aws.s3.enable_path_style_access'='true')"
            )
            await cursor.execute("SET hardening AS DEFAULT STORAGE VOLUME")
            await cursor.execute(f"CREATE DATABASE {name}")
            await cursor.execute(f"CREATE USER {name} IDENTIFIED BY %s", (password,))
            await cursor.execute(f"GRANT ALL ON {name}.* TO USER {name}")
            for table in ("a", "b"):
                await cursor.execute(
                    f"CREATE TABLE {name}.{table} (id INT NOT NULL, value INT) "
                    "PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1"
                )

        @asynccontextmanager
        async def connection(context=None):
            conn = await asyncmy.connect(
                host="127.0.0.1",
                port=int(port),
                user=name,
                password=password,
                database=name,
                autocommit=True,
            )
            try:
                yield conn
            finally:
                conn.close()

        async def version():
            async with root.cursor() as cursor:
                await cursor.execute("SELECT CURRENT_VERSION()")
                return (await cursor.fetchone())[0]

        async def mode():
            async with root.cursor() as cursor:
                await cursor.execute("ADMIN SHOW FRONTEND CONFIG LIKE 'run_mode'")
                names = [item[0].lower() for item in cursor.description]
                return (await cursor.fetchone())[names.index("value")]

        caps = await StarRocksCapabilityProvider().detect(
            "shared-data-fixture", version, mode_reader=mode
        )
        assert caps.transaction_update_delete and caps.identity.deployment_mode == "shared_data"
        yield name, connection, caps
    finally:
        async with root.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS {name}")
            await cursor.execute(f"DROP USER IF EXISTS {name}")
        root.close()


async def run(sqls, database, connection, caps):
    async with connection() as metadata:
        context = ExecutionContext(
            database,
            database=database,
            capabilities=caps,
            confirm_destructive=True,
            connection=metadata,
        )
        repository = QueryRepository()
        context.binder = Binder(StarRocksCatalogProvider(repository, context, lambda: ""))
        steps = [
            await SQLPlanner().plan(
                ast_builders.build(parse_statement(sql)), PlanningContext(database=database)
            )
            for sql in sqls
        ]

        async def engine(plan, ctx):
            return await repository.execute_as_user(
                plan.engine_sql,
                database,
                "",
                database=database,
                connected=ctx.connection,
                session_prepared=True,
            )

        return await SQLExecutor(
            engine, transactions=TransactionRunner(connection, AsyncMock())
        ).execute(CompositePlan(tuple(steps), Atomicity.SINGLE_ENGINE_TRANSACTION), context)


async def test_shared_data_update_delete_and_repeated_insert_commit(shared_data):
    database, connection, caps = shared_data
    await run(
        ["INSERT INTO a VALUES(1,10)", "INSERT INTO b VALUES(2,20)"], database, connection, caps
    )
    result = await run(
        [
            "UPDATE a SET value=11 WHERE id=1",
            "DELETE FROM b WHERE id=2",
            "INSERT INTO a VALUES(3,30)",
            "INSERT INTO a VALUES(4,40)",
        ],
        database,
        connection,
        caps,
    )
    assert result.success
    async with connection() as conn, conn.cursor() as cursor:
        await cursor.execute("SELECT * FROM a ORDER BY id")
        assert await cursor.fetchall() == ((1, 11), (3, 30), (4, 40))
        await cursor.execute("SELECT * FROM b")
        assert await cursor.fetchall() == ()


async def test_shared_data_update_delete_rollback(shared_data):
    database, connection, caps = shared_data
    await run(
        ["INSERT INTO a VALUES(1,10)", "INSERT INTO b VALUES(2,20)"], database, connection, caps
    )
    with pytest.raises(Exception) as failure:
        await run(
            ["UPDATE a SET value=99", "DELETE FROM b", "INSERT INTO a VALUES(1,2,3)"],
            database,
            connection,
            caps,
        )
    assert failure.value.execution_failure["outcome"] == "rolled_back"
    async with connection() as conn, conn.cursor() as cursor:
        await cursor.execute("SELECT * FROM a")
        assert await cursor.fetchall() == ((1, 10),)
        await cursor.execute("SELECT * FROM b")
        assert await cursor.fetchall() == ((2, 20),)
