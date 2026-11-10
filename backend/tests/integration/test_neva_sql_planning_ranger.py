"""Acceptance: Neva's SQL planning check obeys Ranger for the caller's active role.

``validate_sql`` asks the patched FE to plan (EXPLAIN) one SELECT as the caller.
A table the role may read is verified; a table Ranger does not open to the role
fails the check with a plain reason; nothing runs. The user, role, database and
policy are unique to the run and removed afterwards.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.common.nova_system import init_nova_system
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import decode_token
from app.integrations.ranger.client import RangerClient
from app.integrations.ranger.schemas import RangerPolicy
from app.main import create_app
from app.modules.access_control.security_context import SecurityContext
from app.modules.access_control.service import access_control_service
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.validate_sql import validate_sql_tool
from tests.benchmark.business_intelligence.client import StudioClient

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_NEVA_SQL_PLANNING_RANGER_ACCEPTANCE") != "1",
        reason="Creates a user, a role and a Ranger policy on a patched FE; opt in explicitly",
    ),
]


async def _check(user: dict, database: str, sql: str):
    context = SimpleNamespace(user=user, database=database, schema_name=None)
    return await validate_sql_tool.run(
        ToolInvocation("v", "validate_sql", {"sql": sql}), context
    )


async def test_planning_verifies_only_what_ranger_lets_the_role_read():
    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    suffix = uuid4().hex[:10]
    database, role = "nvp_data_" + suffix, "nvp_read_" + suffix
    username, password = "nvp_u_" + suffix, secrets.token_urlsafe(24)
    operator = SecurityContext(principal="nova_admin", active_role="ACCOUNTADMIN")
    ranger, policies, client = RangerClient(), [], None
    try:
        await access_control_service.create_role(operator, role)
        await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
        await access_control_service.assign_role(operator, role=role, username=username)
        await db.execute_system(f"SET DEFAULT ROLE `{role}` TO '{username}'")
        await db.execute_system(f"CREATE DATABASE {database}")
        for table in ("open_sales", "payroll"):
            await db.execute_system(
                f"CREATE TABLE {database}.{table} (id BIGINT, amount DECIMAL(12,2)) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                "PROPERTIES('replication_num'='1')")
        policies.append(await ranger.put_policy(RangerPolicy.model_validate({
            "service": settings.RANGER_SERVICE_NAME,
            "name": f"nvp/{suffix}/open_sales", "policyType": 0,
            "resources": {
                "catalog": {"values": ["default_catalog"]},
                "database": {"values": [database]},
                "table": {"values": ["open_sales"]},
                "column": {"values": ["*"]},
            },
            "policyItems": [{"roles": [role],
                             "accesses": [{"type": "select", "isAllowed": True}]}],
        })))
        client = StudioClient(httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app()), base_url="http://planning.test",
            timeout=120))
        await client.login(username, password, role)
        sid = decode_token(client.http.headers["Authorization"].removeprefix("Bearer "))["sid"]
        user = {**(await session_store.get(sid)), "session_id": sid}

        # Ranger syncs a new policy to the FE after a delay.
        async with asyncio.timeout(90):
            while True:
                allowed = await _check(user, database, f"SELECT amount FROM {database}.open_sales")
                if allowed.data["valid"] and allowed.data["objects_verified"]:
                    break
                await asyncio.sleep(1)
        assert allowed.data["objects_verified"] and allowed.data["executed"] is False

        refused = await _check(user, database, f"SELECT amount FROM {database}.payroll")
        assert refused.data["objects_verified"] and not refused.data["valid"]
        assert "may not read" in refused.summary

        missing = await _check(user, database, f"SELECT town FROM {database}.open_sales")
        assert not missing.data["valid"] and "town" in missing.summary.lower()
    finally:
        original_error = sys.exc_info()[0] is not None
        errors = []

        async def clean(operation):
            try:
                await operation
            except Exception as exc:  # noqa: BLE001 - every cleanup step runs
                errors.append(type(exc).__name__)

        if client is not None:
            token = client.http.headers.get("Authorization", "").removeprefix("Bearer ")
            if token:
                await clean(session_store.delete(decode_token(token)["sid"]))
            await clean(client.http.aclose())
        for policy in reversed(policies):
            await clean(ranger.delete_policy(policy["id"]))
        await clean(db.execute_system(f"DROP DATABASE IF EXISTS {database}"))
        await clean(db.execute_system(f"DROP USER IF EXISTS '{username}'"))
        await clean(access_control_service.drop_role(operator, role))
        await clean(session_store.close())
        await clean(db.close_system_pool())
        if errors and not original_error:
            pytest.fail(f"Acceptance cleanup failed: {errors}")
