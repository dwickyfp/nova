"""Live source-permission checks across the Intelligence read surfaces."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi import HTTPException

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
    feature_store,
)
from app.modules.intelligence.search import SearchIndexCreate, SearchQuery, search_service
from app.modules.intelligence.search_schema import ensure_search_schema
from app.modules.intelligence.semantic_view_schema import ensure_semantic_view_schema
from app.modules.intelligence.semantic_views import (
    SemanticViewCreate,
    SemanticViewQuery,
    semantic_view_service,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_STARROCKS_TEST") != "1",
    reason="Live StarRocks permission test is opt-in",
)


async def test_restricted_user_cannot_read_managed_intelligence_objects():
    suffix = uuid4().hex[:10]
    source = f"NOVA_SYSTEM.INTEL_RBAC_{suffix}"
    index = f"intel_search_{suffix}"
    view = f"intel_view_{suffix}"
    group = f"intel_group_{suffix}"
    username = f"intel_restricted_{suffix}"
    password = f"test_{suffix}_only"
    owner = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    restricted = {
        "username": username,
        "encrypted_password": encrypt_password(password),
        "active_role": None,
        "session_id": None,
    }
    entity_id = None
    semantic_id = None
    await db.init_system_pool()
    try:
        await ensure_search_schema()
        await ensure_feature_schema()
        await ensure_semantic_view_schema()
        await db.execute_system(
            f"CREATE TABLE {source} (id BIGINT NOT NULL, event_ts DATETIME NOT NULL, "
            "content STRING NOT NULL, value INT NOT NULL) DUPLICATE KEY(id,event_ts) "
            'DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"INSERT INTO {source} VALUES (1,'2026-01-01 00:00:00','private running shoe',7)"
        )
        await db.execute_system(f"CREATE USER '{username}' IDENTIFIED BY '{password}'")
        await search_service.create(
            SearchIndexCreate(
                name=index, source_relation=source, key_columns=["id"],
                content_columns=["content"],
            ),
            owner,
        )
        for _ in range(120):
            state = await search_service.describe(index, owner)
            if state["active_version"] == 1:
                break
            assert state["versions"][0]["build_status"] != "FAILED", state
            await asyncio.sleep(0.25)
        assert state["active_version"] == 1
        entity = await entity_registry.create(
            EntityCreate(
                name=f"intel_entity_{suffix}", database="NOVA_SYSTEM",
                relation=source, key_columns=["id"],
            ),
            owner,
        )
        entity_id = entity.id
        await feature_store.create_view(
            FeatureViewDefinition(
                name=view, entity_id=entity_id, source_relation=source,
                event_timestamp="event_ts", feature_columns=["value"],
            ),
            owner,
        )
        await feature_store.create_group(
            FeatureGroupDefinition(
                name=group, entity_id=entity_id,
                members=[FeatureGroupMember(view_name=view, version=1)],
            ),
            owner,
        )
        semantic_name = f"intel_semantic_{suffix}"
        semantic_definition = yaml.safe_load(
            Path("app/modules/agents/examples/nova_sales.ossie.yaml").read_text()
        )
        semantic_definition["name"] = semantic_name
        semantic = await semantic_view_service.create(
            SemanticViewCreate(
                name=semantic_name, database="NOVA_DEMO",
                schema_name=f"rbac_{suffix}",
                definition=yaml.safe_dump(semantic_definition),
            ),
            owner,
        )
        semantic_id = semantic["id"]
        assert (await semantic_view_service.validate(semantic_id, 1, owner))["valid"]
        await semantic_view_service.publish(semantic_id, 1, owner)

        assert (await search_service.query(
            index, SearchQuery(query="shoe", mode="LEXICAL"), owner
        ))["hits"]
        assert (await feature_store.lookup(
            group, FeatureLookup(entity_key={"id": 1}), owner
        ))["values"]["value"] == 7
        assert (await semantic_view_service.query(
            semantic_id, SemanticViewQuery(metrics=["total_revenue"], limit=1), owner
        ))["columns"] == ["total_revenue"]

        with pytest.raises(HTTPException) as search_denied:
            await search_service.query(
                index, SearchQuery(query="shoe", mode="LEXICAL"), restricted
            )
        assert search_denied.value.status_code == 404
        with pytest.raises(HTTPException) as feature_denied:
            await feature_store.lookup(
                group, FeatureLookup(entity_key={"id": 1}), restricted
            )
        assert feature_denied.value.status_code == 404
        with pytest.raises(HTTPException) as semantic_denied:
            await semantic_view_service.query(
                semantic_id, SemanticViewQuery(metrics=["total_revenue"], limit=1),
                restricted,
            )
        assert semantic_denied.value.status_code == 404
    finally:
        if semantic_id:
            await semantic_view_service.drop(semantic_id, owner)
        if await feature_store._group(group):
            await feature_store.drop_group(group, owner)
        if await feature_store._view(view):
            await feature_store.drop_view(view, owner)
        if entity_id:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE id=%s", [entity_id]
            )
        if await search_service._get(index):
            await search_service.drop(index, owner)
        await db.execute_system(f"DROP USER IF EXISTS '{username}'")
        await db.execute_system(f"DROP TABLE IF EXISTS {source}")
        await db.close_system_pool()
