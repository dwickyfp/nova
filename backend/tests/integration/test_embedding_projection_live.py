"""Opt-in provider-to-StarRocks acceptance test for registered text embeddings."""

import hashlib
import json
import os
from uuid import uuid4

import pytest

from app.core.database import db
from app.modules.ai_ml.embeddings import EmbeddingError, embedding_service
from app.modules.intelligence.vector_backend import SearchProjection, vector_backend

pytestmark = pytest.mark.skipif(
    os.environ.get("NOVA_LIVE_EMBEDDING_TEST") != "1",
    reason="Registered provider and StarRocks live test is opt-in",
)


async def test_registered_embedding_model_indexes_and_retrieves_documents():
    alias = os.environ["NOVA_LIVE_EMBEDDING_ALIAS"]
    revision = os.environ["NOVA_LIVE_EMBEDDING_REVISION"]
    model = await embedding_service.resolve_model(alias=alias, revision=revision)
    pinned = await embedding_service.resolve_model(model_id=model.model_id, revision=revision)
    assert pinned.dimensions == model.dimensions
    with pytest.raises(EmbeddingError, match="revision is unavailable"):
        await embedding_service.resolve_model(model_id=model.model_id, revision="invalid-revision")

    projection = SearchProjection(str(uuid4()), 1, pinned.dimensions, pinned.metric)
    documents = [
        ("shoe", "lightweight running shoe"),
        ("warehouse", "warehouse database analytics"),
    ]
    await db.init_system_pool()
    original = await vector_backend.health()
    created = False
    try:
        for flag, enabled in (
            ("enable_experimental_vector", original["vector"]),
            ("enable_experimental_gin", original["full_text"]),
        ):
            if not enabled:
                await db.execute_system(f'ADMIN SET FRONTEND CONFIG ("{flag}"="true")')
        await vector_backend.create_index(projection)
        created = True
        vectors = await embedding_service.embed_batch(
            [content for _, content in documents], pinned
        )
        assert len(vectors) == len(documents)
        assert vectors[0] != vectors[1]
        for (source_key, content), vector in zip(documents, vectors, strict=True):
            literal = "[" + ",".join(str(value) for value in vector) + "]"
            await db.execute_system(
                f"INSERT INTO {projection.table()} "
                "(source_key,content,metadata,embedding,content_hash,model_id,"
                "model_revision,indexed_at) VALUES "
                f"(%s,%s,parse_json(%s),{literal},%s,%s,%s,NOW())",
                [
                    source_key,
                    content,
                    json.dumps({"category": source_key}),
                    hashlib.sha256(content.encode()).hexdigest(),
                    pinned.model_id,
                    pinned.revision,
                ],
            )
        query_vector = await embedding_service.embed(documents[0][1], pinned)
        nearest = await vector_backend.search(projection, query_vector, 2)
        assert nearest["rows"][0][0] == "shoe"
        lexical = await db.execute_system(
            f"SELECT source_key FROM {projection.table()} "
            "WHERE content MATCH_ANY %s ORDER BY source_key LIMIT 10",
            ["running"],
        )
        assert [row[0] for row in lexical["rows"]] == ["shoe"]
    finally:
        try:
            if created:
                await vector_backend.drop_index(projection)
        finally:
            try:
                for flag, enabled in (
                    ("enable_experimental_vector", original["vector"]),
                    ("enable_experimental_gin", original["full_text"]),
                ):
                    if not enabled:
                        await db.execute_system(
                            f'ADMIN SET FRONTEND CONFIG ("{flag}"="false")'
                        )
            finally:
                await db.close_system_pool()
