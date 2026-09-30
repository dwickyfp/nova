import os
from contextlib import suppress
from uuid import uuid4

import asyncmy
import pytest

from app.core.database import db
from app.modules.query.repository import QueryRepository
from app.sql_frontend.binding.catalog import Binder
from app.sql_frontend.binding.models import TableName
from app.sql_frontend.binding.starrocks import StarRocksCatalogProvider
from app.sql_frontend.context import ExecutionContext
from tests.conftest import engine_host_ports

pytestmark = pytest.mark.engine


async def test_frontend_http_native_forms_task_metadata_and_explicit_binding(client, admin_token):
    client.headers["Authorization"] = f"Bearer {admin_token}"
    name = "frontend_" + uuid4().hex[:12]
    task = "frontend_task_" + uuid4().hex[:12]
    role = "frontend_role_" + uuid4().hex[:12]
    database = "NOVA_ANALYTICS"

    async def execute(sql, confirm=False, schema="default"):
        response = await client.post(
            "/api/v1/query/execute",
            json={
                "sql": sql,
                "database": database,
                "schema": schema,
                "confirm_destructive": confirm,
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()[0]
        assert result["success"], result.get("error")
        return result

    try:
        await db.execute_system(f"CREATE ROLE {role}")
        await db.execute_system(f"GRANT ALL ON *.* TO ROLE {role}")
        await db.execute_system(f"GRANT CREATE TABLE ON DATABASE {database} TO ROLE {role}")
        await db.execute_system(f"GRANT {role} TO USER nova_admin")
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "nova_admin",
                "password": os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!"),
            },
        )
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        await execute(f"SET ROLE {role}")
        await execute(
            f"CREATE TABLE {name} (id INT NOT NULL, amount INT DEFAULT '0') PRIMARY KEY(id) "
            "DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES('replication_num'='1')"
        )
        await execute(f"INSERT INTO {name} VALUES (1,10),(2,20)")
        assert (await execute(f"WITH c AS (SELECT * FROM {name}) SELECT SUM(amount) FROM c"))[
            "rows"
        ] == [[30]]
        await execute(f"UPDATE {name} SET amount=11 WHERE id=1", True)
        await execute(f"DELETE FROM {name} WHERE id=2", True)
        assert (await execute(f"SELECT amount FROM {name}"))["rows"] == [[11]]
        assert (await execute("SELECT 'CREATE TASK; @s; ML_PREDICT; UPDATE' AS literal"))[
            "rows"
        ] == [["CREATE TASK; @s; ML_PREDICT; UPDATE"]]
        await execute(
            f"CREATE TABLE {name}_copy PROPERTIES('replication_num'='1') AS SELECT * FROM {name}"
        )
        await execute("SHOW TABLES")
        stage = await execute("SELECT * FROM @products.products_new.csv", schema="public")
        assert stage["row_count"] > 0 and "FILES(" in stage["executed_sql"]
        assert "minioadmin" not in stage["executed_sql"]
        audits = await db.execute_system(
            "SELECT sql_text, rewritten_sql FROM NOVA_SYSTEM.AUDIT_LOG "
            "WHERE user_name='nova_admin' AND rewritten_sql LIKE '%products_new.csv%' "
            "ORDER BY event_time DESC LIMIT 1"
        )
        assert audits["rows"] and "minioadmin" not in str(audits["rows"])
        await execute(f"EXPLAIN SELECT * FROM {name}")
        await execute(
            f"CREATE TASK {database}.default.{task} AS INSERT INTO {database}.{name} VALUES (3,30)"
        )
        metadata = await db.execute_system(
            "SELECT definition, schema_name, owner_role FROM NOVA_SYSTEM.CONFIG_TASKS "
            "WHERE name=%s",
            [task],
        )
        assert len(metadata["rows"]) == 1
        assert "VALUES (3,30)" in metadata["rows"][0][0]
        assert metadata["rows"][0][1] == "default" and metadata["rows"][0][2]
        connection = await asyncmy.connect(
            host="127.0.0.1",
            port=engine_host_ports()["starrocks-fe"],
            user="nova_admin",
            password=os.getenv("NOVA_ADMIN_TEST_PASSWORD", "NovaProxy2026!"),
        )
        try:
            binder = Binder(
                StarRocksCatalogProvider(
                    QueryRepository(),
                    ExecutionContext(
                        "nova_admin", database=database, role=role, connection=connection
                    ),
                    lambda: "",
                )
            )
            columns = await binder.get_columns(TableName(name, database))
            assert [column.name for column in columns] == ["id", "amount"]
            assert not columns[0].nullable and columns[1].default == "0"
            assert (await binder.get_details(TableName(name, database))).primary_key_columns == (
                "id",
            )
        finally:
            connection.close()
    finally:
        with suppress(Exception):
            await db.execute_system("DELETE FROM NOVA_SYSTEM.CONFIG_TASKS WHERE name=%s", [task])
        await db.execute_system(f"DROP TABLE IF EXISTS {database}.{name}_copy")
        await db.execute_system(f"DROP TABLE IF EXISTS {database}.{name}")
        await db.execute_system(f"DROP ROLE IF EXISTS {role}")
