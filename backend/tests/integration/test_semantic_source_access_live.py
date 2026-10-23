"""Semantic metadata probes retain caller authorization without scanning rows."""

import secrets
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.integrations.ranger.client import ranger_client
from app.integrations.ranger.schemas import RangerPolicy, RangerRole, RangerRoleMember
from app.modules.intelligence.semantic_views import semantic_view_service
from tests.integration.test_intelligence_metadata import intelligence_db as intelligence_db

pytestmark = pytest.mark.engine


async def test_source_probe_rechecks_active_role_and_revoked_select(intelligence_db):
    suffix = uuid4().hex[:12]
    database, username = f"probe_{suffix}", f"probe_user_{suffix}"
    reader, denied = f"PROBE_READ_{suffix}", f"PROBE_DENY_{suffix}"
    password = secrets.token_urlsafe(24)
    session_id = None
    policy = None
    created_user = created_reader = created_denied = False
    await db.execute_system(f"CREATE DATABASE `{database}`")
    try:
        await db.execute_system(
            f"CREATE TABLE `{database}`.`source` (id INT) "
            'DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 '
            'PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(f"CREATE ROLE `{reader}`")
        created_reader = True
        await db.execute_system(f"CREATE ROLE `{denied}`")
        created_denied = True
        await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY %s", [password])
        created_user = True
        await db.execute_system(f"GRANT `{reader}`, `{denied}` TO USER '{username}'")
        await db.execute_system(f"SET DEFAULT ROLE `{reader}` TO '{username}'")
        await ranger_client.put_user_attributes(username, {})
        for role in (reader, denied):
            await ranger_client.put_role(
                RangerRole(name=role, users=[RangerRoleMember(name=username)])
            )
        policy = await ranger_client.put_policy(RangerPolicy.model_validate({
            "name": f"source-probe/{suffix}", "service": settings.RANGER_SERVICE_NAME,
            "resources": {
                "catalog": {"values": ["default_catalog"]},
                "database": {"values": [database]}, "table": {"values": ["*"]},
                "column": {"values": ["*"]},
            },
            "policyItems": [{"roles": [reader],
                             "accesses": [{"type": "select", "isAllowed": True}]}],
        }))
        await db.execute_system(f"GRANT SELECT ON TABLE `{database}`.`source` TO ROLE `{reader}`")
        encrypted_password = encrypt_password(password)
        session_id = await session_store.create(
            username, encrypted_password, [reader, denied], default_role=reader, active_role=reader,
        )
        user = {**await session_store.get(session_id), "session_id": session_id}
        definition = {"datasets": [{"source": f"{database}.source"}]}
        assert await semantic_view_service._source_access(definition, user)

        await session_store.set_active_role(session_id, denied)
        user = {**await session_store.get(session_id), "session_id": session_id}
        assert not await semantic_view_service._source_access(definition, user)

        await session_store.set_active_role(session_id, reader)
        user = {**await session_store.get(session_id), "session_id": session_id}
        assert await semantic_view_service._source_access(definition, user)
        await ranger_client.delete_policy(policy["id"])
        policy.pop("id")
        assert not await semantic_view_service._source_access(definition, user)
        policy = await ranger_client.put_policy(RangerPolicy.model_validate(policy))
        assert await semantic_view_service._source_access(definition, user)
        assert not await semantic_view_service._source_access(
            {"datasets": [{"source": f"{database}.missing"}]}, user,
        )
        audit = await db.execute_system(
            "SELECT active_role,status FROM NOVA_SYSTEM.AUDIT_LOG "
            "WHERE user_name=%s AND event_type='query'",
            [username],
        )
        assert {(reader, "SUCCESS"), (reader, "ERROR")} <= {
            tuple(row) for row in audit["rows"]
        }
        assert not any(row[0] == denied for row in audit["rows"])
    finally:
        if session_id:
            await session_store.delete(session_id)
        if policy and policy.get("id"):
            await ranger_client.delete_policy(policy["id"])
        for role in (reader, denied):
            await ranger_client.delete_role(role)
        if created_user:
            await db.execute_system(f"DROP USER '{username}'")
        if created_reader:
            await db.execute_system(f"DROP ROLE `{reader}`")
        if created_denied:
            await db.execute_system(f"DROP ROLE `{denied}`")
        await db.execute_system(f"DROP DATABASE IF EXISTS `{database}` FORCE")
