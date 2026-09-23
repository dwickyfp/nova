"""Vector adapter keeps StarRocks syntax and capability checks behind one boundary."""

import pytest

from app.modules.intelligence import vector_backend as module
from app.modules.intelligence.vector_backend import (
    SearchProjection,
    StarRocksVectorBackend,
    VectorBackendError,
)

PROJECTION = SearchProjection("93b5079d-7fc1-4553-8fa9-189d0a6edbc1", 2, 3, "cosine")


def test_projection_name_is_internal_and_versioned():
    assert PROJECTION.table() == ("`_NOVA_AI_SEARCH`.`IDX_93b5079d7fc145538fa9189d0a6edbc1_V2`")
    with pytest.raises(VectorBackendError):
        SearchProjection("bad;drop", 1, 3, "cosine").table()
    with pytest.raises(VectorBackendError):
        SearchProjection(PROJECTION.index_id, 0, 3, "cosine").table()


def test_metric_direction_and_dimension_validation():
    cosine = StarRocksVectorBackend.vector_sql(PROJECTION, [1, 0, 0], 5)
    assert "approx_cosine_similarity" in cosine
    assert "DESC LIMIT 5" in cosine
    l2 = StarRocksVectorBackend.vector_sql(
        SearchProjection(PROJECTION.index_id, 1, 3, "l2"), [1, 0, 0], 5
    )
    assert "approx_l2_distance" in l2
    assert "ASC LIMIT 5" in l2
    for vector, top_k in [([1, 0], 5), ([float("nan"), 0, 0], 5), ([1, 0, 0], 0)]:
        with pytest.raises(VectorBackendError):
            StarRocksVectorBackend.vector_sql(PROJECTION, vector, top_k)
    with pytest.raises(VectorBackendError):
        SearchProjection(PROJECTION.index_id, 1, 3, "not-a-metric")


async def test_health_reads_both_required_flags(monkeypatch):
    async def execute(sql):
        value = "false" if "experimental_gin" in sql else "true"
        return {"rows": [["flag", "[]", value]]}

    monkeypatch.setattr(module.db, "execute_system", execute)
    assert await StarRocksVectorBackend().health() == {"vector": True, "full_text": False}


async def test_creation_fails_closed_when_capability_is_off(monkeypatch):
    backend = StarRocksVectorBackend()
    calls = []

    async def health():
        return {"vector": False, "full_text": True}

    async def execute(sql):
        calls.append(sql)

    monkeypatch.setattr(backend, "health", health)
    monkeypatch.setattr(module.db, "execute_system", execute)
    with pytest.raises(VectorBackendError, match="must be enabled"):
        await backend.create_index(PROJECTION)
    assert not calls


async def test_creation_encapsulates_hnsw_and_gin_sql(monkeypatch):
    backend = StarRocksVectorBackend()
    calls = []

    async def health():
        return {"vector": True, "full_text": True}

    async def execute(sql):
        calls.append(sql)

    monkeypatch.setattr(backend, "health", health)
    monkeypatch.setattr(module.db, "execute_system", execute)
    await backend.create_index(PROJECTION)
    assert calls[0] == "CREATE DATABASE IF NOT EXISTS `_NOVA_AI_SEARCH`"
    assert 'USING VECTOR ("index_type"="hnsw"' in calls[1]
    assert 'USING GIN ("parser"="english"' in calls[1]
    assert '"dim"="3"' in calls[1]
