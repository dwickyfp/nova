"""Optional live Feature Store lifecycle and point-in-time contract."""

import os
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.intelligence.entities import EntityCreate, entity_registry
from app.modules.intelligence.feature_schema import ensure_feature_schema
from app.modules.intelligence.feature_store import (
    FeatureGroupDefinition,
    FeatureGroupMember,
    FeatureLookup,
    FeatureViewDefinition,
    OnlineMaterializeRequest,
    TrainingSetCreate,
    feature_store,
    online_store,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_STARROCKS_TEST") != "1", reason="Live StarRocks test is opt-in"
)


async def test_feature_store_lifecycle_and_no_future_leakage():
    suffix = uuid4().hex[:12]
    source = f"NOVA_SYSTEM.FEATURE_SOURCE_{suffix}"
    labels = f"NOVA_SYSTEM.FEATURE_LABELS_{suffix}"
    view = f"feature_view_{suffix}"
    view_two = f"feature_view_two_{suffix}"
    group = f"feature_group_{suffix}"
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    entity_id = None
    await db.init_system_pool()
    try:
        await ensure_feature_schema()
        await db.execute_system(
            f"CREATE TABLE {source} (customer_id BIGINT NOT NULL, event_ts DATETIME NOT NULL, "
            "orders_7d INT NOT NULL, visits_7d INT NOT NULL) "
            "DUPLICATE KEY(customer_id,event_ts) "
            'DISTRIBUTED BY HASH(customer_id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"CREATE TABLE {labels} (customer_id BIGINT NOT NULL, label_ts DATETIME NOT NULL, "
            "churn INT NOT NULL) DUPLICATE KEY(customer_id,label_ts) "
            'DISTRIBUTED BY HASH(customer_id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"INSERT INTO {source} VALUES "
            "(1,'2026-01-01 00:00:00',7,3),(1,'2026-01-03 00:00:00',99,42)"
        )
        await db.execute_system(f"INSERT INTO {labels} VALUES (1,'2026-01-02 00:00:00',1)")
        entity = await entity_registry.create(
            EntityCreate(
                name=f"feature_entity_{suffix}",
                database="NOVA_SYSTEM",
                relation=source,
                key_columns=["customer_id"],
            ),
            user,
        )
        entity_id = entity.id
        created = await feature_store.create_view(
            FeatureViewDefinition(
                name=view,
                entity_id=entity.id,
                source_relation=source,
                event_timestamp="event_ts",
                feature_columns=["orders_7d"],
            ),
            user,
        )
        assert created["active_version"] == 1
        created_group = await feature_store.create_group(
            FeatureGroupDefinition(
                name=group,
                entity_id=entity.id,
                members=[FeatureGroupMember(view_name=view, version=1)],
            ),
            user,
        )
        assert created_group["active_version"] == 1
        as_of = await feature_store.lookup(
            group,
            FeatureLookup(entity_key={"customer_id": 1}, as_of="2026-01-02T00:00:00"),
            user,
        )
        assert as_of["values"]["orders_7d"] == 7
        materialized = await feature_store.materialize_online(
            group,
            OnlineMaterializeRequest(entity_keys=[{"customer_id": 1}, {"customer_id": 999}]),
            user,
        )
        assert materialized["published"] == 1
        assert materialized["missing"] == 1
        latest = await feature_store.lookup(
            group, FeatureLookup(entity_key={"customer_id": 1}), user
        )
        assert latest["values"]["orders_7d"] == 99
        assert latest["source"] == "online"
        assert (
            await feature_store.lookup(group, FeatureLookup(entity_key={"customer_id": 1}), user)
        )["source"] == "online"
        training = await feature_store.create_training_set(
            group,
            TrainingSetCreate(
                label_relation=labels,
                entity_keys=["customer_id"],
                event_timestamp="label_ts",
                label_columns=["churn"],
                preview_rows=1,
            ),
            user,
        )
        assert training["preview"]["rows"][0][-1] == 7
        refreshed = await feature_store.refresh_view(view, user)
        assert refreshed["version"] == 2 and refreshed["status"] == "READY"
        assert (await feature_store._view(view))["active_version"] == 1
        await feature_store.activate_view(view, 2, user)
        new_group_version = await feature_store.add_group_version(
            group, [FeatureGroupMember(view_name=view, version=2)], user
        )
        assert new_group_version["status"] == "DRAFT"
        await feature_store.activate_group(group, 2, user)
        assert (
            await feature_store.lookup(group, FeatureLookup(entity_key={"customer_id": 1}), user)
        )["version"] == 2
        await feature_store.create_view(
            FeatureViewDefinition(
                name=view_two,
                entity_id=entity.id,
                source_relation=source,
                event_timestamp="event_ts",
                feature_columns=["visits_7d"],
            ),
            user,
        )
        await feature_store.add_group_version(
            group,
            [
                FeatureGroupMember(view_name=view, version=2),
                FeatureGroupMember(view_name=view_two, version=1),
            ],
            user,
        )
        await feature_store.activate_group(group, 3, user)
        combined = await feature_store.create_training_set(
            group,
            TrainingSetCreate(
                label_relation=labels,
                entity_keys=["customer_id"],
                event_timestamp="label_ts",
                label_columns=["churn"],
                preview_rows=1,
            ),
            user,
        )
        assert combined["preview"]["rows"][0][-2:] == [7, 3]
    finally:
        await online_store.delete(group, 1, {"customer_id": 1})
        await online_store.delete(group, 2, {"customer_id": 1})
        await online_store.delete(group, 3, {"customer_id": 1})
        if await feature_store._group(group):
            await feature_store.drop_group(group, user)
        if await feature_store._view(view_two):
            await feature_store.drop_view(view_two, user)
        if await feature_store._view(view):
            await feature_store.drop_view(view, user)
        if entity_id:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE id=%s", [entity_id]
            )
        await db.execute_system(f"DROP TABLE IF EXISTS {labels}")
        await db.execute_system(f"DROP TABLE IF EXISTS {source}")
        await db.close_system_pool()
