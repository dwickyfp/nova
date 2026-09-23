"""Optional live StarRocks contract for the shared entity registry."""

import os
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.intelligence.entities import EntityCreate, entity_registry

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_STARROCKS_TEST") != "1", reason="Live StarRocks test is opt-in"
)


async def test_entity_create_read_deprecate_with_caller_source_probe():
    suffix = uuid4().hex[:12]
    table = f"NOVA_SYSTEM.NOVA_ENTITY_TEST_{suffix}"
    name = f"entity_test_{suffix}"
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    await db.init_system_pool()
    entity_id = None
    try:
        await db.execute_system(
            f"CREATE TABLE {table} (customer_id BIGINT NOT NULL, tenant_id BIGINT NOT NULL) "
            "DUPLICATE KEY(customer_id) DISTRIBUTED BY HASH(customer_id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        body = EntityCreate(
            name=name,
            database="NOVA_SYSTEM",
            relation=table,
            key_columns=["customer_id", "tenant_id"],
        )
        created = await entity_registry.create(body, user)
        entity_id = created.id
        assert created.relation == table
        assert created.key_columns == ["customer_id", "tenant_id"]
        assert (await entity_registry.get(created.id, user)).id == created.id
        assert any(item.id == created.id for item in await entity_registry.list(user))
        assert await entity_registry.deprecate(created.id, user)
        assert await entity_registry.get(created.id, user) is None
    finally:
        if entity_id:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE id=%s", [entity_id]
            )
        await db.execute_system(f"DROP TABLE IF EXISTS {table}")
        await db.close_system_pool()
