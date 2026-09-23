"""Optional end-to-end Feature Group training through the existing ML engine."""

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
    FeatureTrainRequest,
    FeatureViewDefinition,
    feature_store,
)
from app.modules.ml_engine.service import ml_engine_service

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_FEATURE_ML_TEST") != "1",
    reason="Live Feature Store and ML engine test is opt-in",
)


async def test_feature_group_trains_model_with_pinned_lineage():
    suffix = uuid4().hex[:12]
    source = f"NOVA_SYSTEM.FEATURE_ML_SOURCE_{suffix}"
    labels = f"NOVA_SYSTEM.FEATURE_ML_LABELS_{suffix}"
    view = f"feature_ml_view_{suffix}"
    group = f"feature_ml_group_{suffix}"
    model_name = f"feature_ml_model_{suffix}"
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    entity_id = None
    model_id = None
    training_id = None
    await db.init_system_pool()
    try:
        await ensure_feature_schema()
        await db.execute_system(
            f"CREATE TABLE {source} (customer_id BIGINT NOT NULL,event_ts DATETIME NOT NULL,"
            "orders_7d INT NOT NULL) DUPLICATE KEY(customer_id,event_ts) "
            'DISTRIBUTED BY HASH(customer_id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"CREATE TABLE {labels} (customer_id BIGINT NOT NULL,label_ts DATETIME NOT NULL,"
            "churn INT NOT NULL) DUPLICATE KEY(customer_id,label_ts) "
            'DISTRIBUTED BY HASH(customer_id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        feature_values = ",".join(
            f"({i},'2026-01-01 00:00:00',{i % 10})" for i in range(1, 41)
        )
        label_values = ",".join(
            f"({i},'2026-01-02 00:00:00',{i % 2})" for i in range(1, 41)
        )
        await db.execute_system(f"INSERT INTO {source} VALUES {feature_values}")
        await db.execute_system(f"INSERT INTO {labels} VALUES {label_values}")
        entity = await entity_registry.create(
            EntityCreate(
                name=f"feature_ml_entity_{suffix}", database="NOVA_SYSTEM",
                relation=source, key_columns=["customer_id"],
            ), user,
        )
        entity_id = entity.id
        await feature_store.create_view(
            FeatureViewDefinition(
                name=view, entity_id=entity.id, source_relation=source,
                event_timestamp="event_ts", feature_columns=["orders_7d"],
            ), user,
        )
        await feature_store.create_group(
            FeatureGroupDefinition(
                name=group, entity_id=entity.id,
                members=[FeatureGroupMember(view_name=view, version=1)],
            ), user,
        )
        trained = await feature_store.train(
            group,
            FeatureTrainRequest(
                label_relation=labels, entity_keys=["customer_id"],
                event_timestamp="label_ts", label_columns=["churn"],
                model_name=model_name, model_type="classification",
                target_column="churn", test_size=0.25,
            ), user,
        )
        model_id = trained["model"]["model_id"]
        training_id = trained["training_set"]["id"]
        assert trained["model"]["training_rows"] == 40
        assert trained["model"]["feature_columns"] == ["orders_7d"]
        link = await db.execute_system(
            "SELECT group_version,lineage FROM NOVA_SYSTEM.CONFIG_FEATURE_ML_LINKS "
            "WHERE model_id=%s", [model_id]
        )
        assert link["rows"][0][0] == 1
        assert "behavior" not in str(link["rows"][0][1])
        assert view in str(link["rows"][0][1])
    finally:
        if model_id:
            await ml_engine_service.delete_model(model_id, owner_name=user["username"])
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_FEATURE_ML_LINKS WHERE model_id=%s",
                [model_id],
            )
        if training_id:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_FEATURE_TRAINING_SETS WHERE id=%s",
                [training_id],
            )
        if await feature_store._group(group):
            await feature_store.drop_group(group, user)
        if await feature_store._view(view):
            await feature_store.drop_view(view, user)
        if entity_id:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE id=%s", [entity_id]
            )
        await db.execute_system(f"DROP TABLE IF EXISTS {labels}")
        await db.execute_system(f"DROP TABLE IF EXISTS {source}")
        await db.close_system_pool()
