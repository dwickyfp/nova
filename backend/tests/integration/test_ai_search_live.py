"""Optional live AI Search lifecycle with a configured embedding model."""

import asyncio
import os
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.database import db
from app.core.security import encrypt_password
from app.modules.ai_ml.embeddings import embedding_service
from app.modules.intelligence.search import (
    SearchEvalCase,
    SearchEvalRequest,
    SearchIndexCreate,
    SearchQuery,
    SearchRebuild,
    search_service,
)
from app.modules.intelligence.search_schema import ensure_search_schema
from app.modules.intelligence.vector_backend import vector_backend

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_AI_SEARCH_TEST") != "1",
    reason="Live AI Search with a configured embedding provider is opt-in",
)


async def test_index_build_query_rebuild_and_activation(monkeypatch):
    suffix = uuid4().hex[:12]
    name = f"search_test_{suffix}"
    source = f"NOVA_SYSTEM.NOVA_SEARCH_TEST_{suffix}"
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    await db.init_system_pool()
    original = await vector_backend.health()
    created = False
    try:
        await ensure_search_schema()
        for flag, enabled in (
            ("enable_experimental_vector", original["vector"]),
            ("enable_experimental_gin", original["full_text"]),
        ):
            if not enabled:
                await db.execute_system(f'ADMIN SET FRONTEND CONFIG ("{flag}"="true")')
        await db.execute_system(
            f"CREATE TABLE {source} (id BIGINT NOT NULL, content STRING NOT NULL, "
            "category VARCHAR(32)) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) "
            'BUCKETS 1 PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(
            f"INSERT INTO {source} VALUES (1,'lightweight running shoe','shoes'),"
            "(2,'warehouse database analytics','software')"
        )
        index = await search_service.create(
            SearchIndexCreate(
                name=name,
                source_relation=source,
                key_columns=["id"],
                content_columns=["content"],
                filter_columns=["category"],
                model_alias="nova.embedding.default",
            ),
            user,
        )
        created = True
        assert index["active_version"] is None
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["active_version"] == 1:
                break
            assert state["versions"][0]["build_status"] != "FAILED", state
            await asyncio.sleep(0.25)
        assert state["active_version"] == 1
        for mode in ("LEXICAL", "SEMANTIC", "HYBRID"):
            result = await search_service.query(
                name,
                SearchQuery(
                    query="running shoe",
                    mode=mode,
                    top_k=2,
                    filters={"category": "shoes"},
                ),
                user,
            )
            assert result["hits"][0]["source_key"] == "[1]", result
        evaluation = await search_service.evaluate(
            name,
            SearchEvalRequest(
                mode="LEXICAL",
                top_k=2,
                cases=[SearchEvalCase(query="running shoe", relevant={"[1]": 2})],
            ),
            user,
        )
        assert evaluation["summary"]["mrr"] == 1.0
        batches = []
        original_embed = embedding_service.embed_batch

        async def counted_embed(texts, model):
            batches.append(len(texts))
            return await original_embed(texts, model)

        monkeypatch.setattr(embedding_service, "embed_batch", counted_embed)
        rebuilt = await search_service.rebuild(name, SearchRebuild(), user)
        assert rebuilt["version"] == 2
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["versions"][0]["build_status"] == "READY":
                break
            assert state["versions"][0]["build_status"] != "FAILED", state
            await asyncio.sleep(0.25)
        assert state["active_version"] == 1
        assert batches == []
        await search_service.activate(name, 2, user)
        assert (await search_service.describe(name, user))["active_version"] == 2
        await db.execute_system(f"INSERT INTO {source} VALUES (3,'trail running sandal','shoes')")
        await search_service._reconcile()
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["active_version"] == 3:
                break
            assert state["versions"][0]["build_status"] != "FAILED", state
            await asyncio.sleep(0.25)
        assert state["active_version"] == 3
        assert batches == [1]
        updated = await search_service.query(
            name,
            SearchQuery(query="trail sandal", mode="LEXICAL", top_k=3),
            user,
        )
        assert any(hit["source_key"] == "[3]" for hit in updated["hits"])
        await db.execute_system(f"DELETE FROM {source} WHERE id=3")
        await search_service._reconcile()
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["active_version"] == 4:
                break
            assert state["versions"][0]["build_status"] != "FAILED", state
            await asyncio.sleep(0.25)
        assert state["active_version"] == 4
        assert batches == [1]
        removed = await search_service.query(
            name, SearchQuery(query="trail sandal", mode="LEXICAL", top_k=3), user
        )
        assert all(hit["source_key"] != "[3]" for hit in removed["hits"])
        await db.execute_system(f"INSERT INTO {source} VALUES (4,'hiking boot','shoes')")

        async def provider_failure(_texts, _model):
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(embedding_service, "embed_batch", provider_failure)
        await search_service._reconcile()
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["versions"][0]["build_status"] == "FAILED":
                break
            await asyncio.sleep(0.25)
        assert state["active_version"] == 4
        assert state["versions"][0]["build_status"] == "FAILED"
        monkeypatch.setattr(embedding_service, "embed_batch", counted_embed)
        await search_service.retry(name, 5, user)
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["versions"][0]["build_status"] == "READY":
                break
            await asyncio.sleep(0.25)
        assert state["active_version"] == 4
        assert state["versions"][0]["build_status"] == "READY"
        await search_service.activate(name, 5, user)
        assert (await search_service.describe(name, user))["active_version"] == 5
    finally:
        try:
            if created:
                await search_service.drop(name, user)
            await db.execute_system(f"DROP TABLE IF EXISTS {source}")
        finally:
            for flag, enabled in (
                ("enable_experimental_vector", original["vector"]),
                ("enable_experimental_gin", original["full_text"]),
            ):
                if not enabled:
                    await db.execute_system(f'ADMIN SET FRONTEND CONFIG ("{flag}"="false")')
            await db.close_system_pool()


async def test_background_poller_reconciles_source_without_manual_trigger():
    suffix = uuid4().hex[:12]
    name = f"search_auto_{suffix}"
    source = f"NOVA_SYSTEM.NOVA_SEARCH_AUTO_{suffix}"
    user = {
        "username": settings.STARROCKS_ROOT_USER,
        "encrypted_password": encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
        "active_role": None,
        "session_id": None,
    }
    await db.init_system_pool()
    try:
        await ensure_search_schema()
        await db.execute_system(
            f"CREATE TABLE {source} (id BIGINT NOT NULL, content STRING NOT NULL) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
            'PROPERTIES("replication_num"="1")'
        )
        await db.execute_system(f"INSERT INTO {source} VALUES (1,'first document')")
        await search_service.create(
            SearchIndexCreate(
                name=name, source_relation=source, key_columns=["id"],
                content_columns=["content"],
            ),
            user,
        )
        await search_service.start()
        for _ in range(120):
            state = await search_service.describe(name, user)
            if state["active_version"] == 1:
                break
            await asyncio.sleep(0.25)
        assert state["active_version"] == 1
        await db.execute_system(f"INSERT INTO {source} VALUES (2,'second document')")
        for _ in range(180):
            state = await search_service.describe(name, user)
            if state["active_version"] == 2:
                break
            await asyncio.sleep(0.5)
        assert state["active_version"] == 2, state
        result = await search_service.query(
            name, SearchQuery(query="second", mode="LEXICAL"), user
        )
        assert result["hits"][0]["source_key"] == "[2]"
    finally:
        await search_service.stop()
        if await search_service._get(name):
            await search_service.drop(name, user)
        await db.execute_system(f"DROP TABLE IF EXISTS {source}")
        await db.close_system_pool()
