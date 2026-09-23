"""Guards that keep draft data and unauthorized projections out of read paths."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.intelligence.feature_store import FeatureViewDefinition
from app.modules.intelligence.search import SearchIndexCreate, SearchQuery, search_service
from app.modules.intelligence.semantic_views import SemanticViewQuery, semantic_view_service


@pytest.mark.parametrize(
    "columns",
    [["id"], ["content", "CONTENT"], ["unsafe-name"]],
)
def test_search_rejects_ambiguous_or_unsafe_source_columns(columns):
    with pytest.raises(ValidationError):
        SearchIndexCreate(
            name="catalog", source_relation="NOVA_SYSTEM.docs",
            key_columns=["id"], content_columns=columns,
        )


def test_feature_view_rejects_duplicate_columns():
    with pytest.raises(ValidationError):
        FeatureViewDefinition(
            name="orders", entity_id="entity-1", source_relation="NOVA_SYSTEM.orders",
            event_timestamp="at", feature_columns=["amount", "AMOUNT"],
        )


async def test_search_denies_projection_when_source_access_is_revoked(monkeypatch):
    async def index(_name):
        return {"status": "ACTIVE", "source_relation": "NOVA_SYSTEM.secret"}

    async def denied(_definition, _user):
        return False

    monkeypatch.setattr(search_service, "_get", index)
    monkeypatch.setattr(search_service, "_source_access", denied)
    with pytest.raises(HTTPException) as exc:
        await search_service.query("secret", SearchQuery(query="document"), {"username": "u"})
    assert exc.value.status_code == 404


async def test_semantic_view_rejects_explicit_draft_query(monkeypatch):
    async def visible(_id, _user):
        return {"active_version": 1}

    async def version(_id, _version):
        return {"status": "DRAFT", "definition": {}}

    monkeypatch.setattr(semantic_view_service, "_visible", visible)
    monkeypatch.setattr(semantic_view_service, "_version", version)
    with pytest.raises(HTTPException) as exc:
        await semantic_view_service.query(
            "view", SemanticViewQuery(metrics=["revenue"], version=2), {"username": "u"}
        )
    assert exc.value.status_code == 404
