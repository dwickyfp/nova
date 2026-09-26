"""Disposable accounts on the isolated engine, through real Nova services."""

from types import SimpleNamespace
from uuid import uuid4

import asyncmy
from fastapi.security import HTTPAuthorizationCredentials
import pytest

from app.common.user_flags import is_must_change_password
from app.core.database import db
from app.core.deps import get_current_user
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.provision_user import ProvisionUserTool
from app.modules.users.service import user_service
from tests.conftest import engine_host_ports, require_stack

pytestmark = pytest.mark.engine


async def test_typed_provisioning_default_role_and_first_login(client, admin_token, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RANGER_ENABLED", False)
    client.headers["Authorization"] = f"Bearer {admin_token}"
    switched = await client.post("/api/v1/auth/switch-role", json={"role": "ACCOUNTADMIN"})
    assert switched.status_code == 200
    caller = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=admin_token))
    username = f"nove.audit.{uuid4().hex[:12]}"
    role = f"nove_empty_{uuid4().hex[:12]}"
    await user_service.create_role(role)
    password = uuid4().hex + "Aa1!"
    ctx = SimpleNamespace(user=caller, secure_input={"password": password},
                          audit_session_id=caller["session_id"])
    try:
        result = await ProvisionUserTool().run(
            ToolInvocation("create", "provision_user", {"username": username, "role": role}), ctx
        )
        assert result.ok, result.error
        assert ctx.secure_input is None and password not in repr(result)
        assert await is_must_change_password(username)
        response = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "PASSWORD_CHANGE_REQUIRED"
        async with db.user_conn(username, password) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT CURRENT_ROLE()")
                assert (await cursor.fetchone())[0] == role
        assert password not in response.text
    finally:
        await user_service.drop_user(username)
        await user_service.drop_role(role)
        await db.execute_system("DELETE FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES WHERE user_name=%s", [username])


async def test_sql_card_account_batch_redacts_password_and_stops_on_error(client, admin_token):
    username = f"nove.sql.{uuid4().hex[:12]}"
    role = f"nove_empty_{uuid4().hex[:12]}"
    await user_service.create_role(role)
    password = uuid4().hex + "Aa1!"
    client.headers["Authorization"] = f"Bearer {admin_token}"
    switched = await client.post("/api/v1/auth/switch-role", json={"role": "ACCOUNTADMIN"})
    assert switched.status_code == 200
    try:
        sql = (
            f"CREATE USER '{username}' IDENTIFIED BY '<temporary_password>';"
            f"ALTER USER '{username}' REQUIRE PASSWORD CHANGE;"
            f"GRANT {role} TO USER '{username}';"
            f"SET DEFAULT ROLE {role} TO '{username}';"
        )
        response = await client.post("/api/v1/query/execute", json={"sql": sql, "temporary_password": password})
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 4 and all(r["success"] for r in results), [r.get("error") for r in results]
        assert password not in response.text
        assert await is_must_change_password(username)

        response = await client.post("/api/v1/query/execute", json={
            "sql": f"SELECT missing_column FROM missing_table; DROP USER '{username}';",
            "confirm_destructive": True,
        })
        assert len(response.json()) == 1 and not response.json()[0]["success"]
        assert await user_service.user_exists(username)
    finally:
        await user_service.drop_user(username)
        await user_service.drop_role(role)
        await db.execute_system("DELETE FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES WHERE user_name=%s", [username])


async def test_native_sql_families_on_disposable_data(docker_services):
    require_stack(docker_services)
    connection = await asyncmy.connect(host="127.0.0.1", port=engine_host_ports()["starrocks-fe"],
                                       user="root", password="")
    database = f"nove_sql_audit_{uuid4().hex[:12]}"
    async with connection.cursor() as cursor:
        await cursor.execute(f"CREATE DATABASE `{database}`")
        try:
            await cursor.execute(f"USE `{database}`")
            for name, model, columns in [
                ("accounts", "PRIMARY KEY(id)", "id BIGINT NOT NULL, name VARCHAR(30), amount INT"),
                ("events", "DUPLICATE KEY(id)", "id BIGINT, amount INT"),
                ("latest", "UNIQUE KEY(id)", "id BIGINT, amount INT"),
                ("totals", "AGGREGATE KEY(id)", "id BIGINT, amount INT SUM"),
            ]:
                await cursor.execute(f"CREATE TABLE {name} ({columns}) {model} "
                                     'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")')
            for sql in [
                'CREATE TABLE daily_events (event_date DATE NOT NULL, event_id BIGINT, amount INT) '
                "DUPLICATE KEY(event_date,event_id) PARTITION BY date_trunc('day',event_date) "
                'DISTRIBUTED BY HASH(event_id) BUCKETS 1 PROPERTIES("replication_num"="1")',
                "INSERT INTO daily_events VALUES ('2026-09-25',1,7),('2026-09-26',2,8)",
                "INSERT INTO accounts VALUES(1,'a',10),(2,'b',20)",
                "UPDATE accounts SET amount=25 WHERE id=2",
                "UPDATE accounts SET amount=99 WHERE id=99",
                "INSERT INTO events VALUES(1,3),(1,4),(2,8)",
                "INSERT INTO latest VALUES(1,10),(1,20)",
                "INSERT INTO totals VALUES(1,10),(1,20)",
                'CREATE TABLE copied PROPERTIES("replication_num"="1") AS SELECT * FROM accounts',
                "CREATE TABLE empty_accounts LIKE accounts",
                "ALTER TABLE empty_accounts ADD COLUMN status VARCHAR(20)",
                "CREATE VIEW v_totals AS SELECT id,SUM(amount) AS total FROM events GROUP BY id",
            ]:
                await cursor.execute(sql)
            cases = [
                ("SELECT SUM(amount) FROM daily_events", 15),
                ("SELECT COUNT(*) FROM accounts", 2),
                ("SELECT amount FROM accounts WHERE id=2", 25),
                ("SELECT total FROM v_totals WHERE id=1", 7),
                ("WITH t AS (SELECT SUM(amount) AS total FROM accounts) SELECT total FROM t", 35),
                ("SELECT amount FROM (SELECT amount,ROW_NUMBER() OVER(ORDER BY amount DESC) rn FROM accounts) t WHERE rn=1", 25),
                ("SELECT COUNT(*) FROM accounts a WHERE NOT EXISTS(SELECT 1 FROM events e WHERE a.id=e.id)", 0),
                ("SELECT ARRAY_LENGTH([1,2,3])", 3),
                ("SELECT GET_JSON_STRING('{\"city\":\"Jakarta\"}','$.city')", "Jakarta"),
                ("SELECT COALESCE(1/NULLIF(0,0),-1)", -1),
                ("SELECT COUNT(*) FROM (SELECT id FROM accounts UNION ALL SELECT id FROM accounts) t", 4),
                ("SELECT SUM(amount) FROM totals", 30),
            ]
            for sql, expected in cases:
                await cursor.execute(sql)
                assert (await cursor.fetchone())[0] == expected, sql
            await cursor.execute("SELECT id,SUM(amount) FROM events GROUP BY GROUPING SETS((id),())")
            assert len(await cursor.fetchall()) == 3
            for sql in ["EXPLAIN SELECT * FROM accounts", "SHOW TABLES", "DESCRIBE accounts", "SHOW CREATE TABLE accounts"]:
                await cursor.execute(sql)
                assert await cursor.fetchall()
            await cursor.execute("DELETE FROM accounts WHERE id=1")
            await cursor.execute("SELECT COUNT(*) FROM accounts")
            assert (await cursor.fetchone())[0] == 1
        finally:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{database}` FORCE")
            connection.close()
