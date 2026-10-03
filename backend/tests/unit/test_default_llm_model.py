from unittest.mock import AsyncMock

import pytest

from app.modules.ai_ml import default_model
from app.modules.ai_ml.service import ai_service
from app.modules.assistant.provider import AssistantProviderClient, AssistantProviderError


@pytest.fixture
def registry(monkeypatch):
    provider = {"id": "p1", "type": "openai", "is_active": True,
                "has_api_key": True, "endpoint": "https://example.com/v1"}
    model = {"id": "m1", "provider_id": "p1", "name": "deepseek-v4-1-flash",
             "type": "llm", "is_active": True}
    monkeypatch.setattr(ai_service, "list_providers", AsyncMock(return_value=[provider]))
    monkeypatch.setattr(ai_service, "get_provider", AsyncMock(return_value=provider))
    monkeypatch.setattr(ai_service, "get_model", AsyncMock(return_value=model))
    monkeypatch.setattr(ai_service, "list_models", AsyncMock(return_value=[
        {**model, "id": "m0", "name": "other"}, model,
    ]))
    monkeypatch.setattr(ai_service, "get_provider_api_key", AsyncMock(return_value="test-key"))
    read = AsyncMock(return_value=default_model.DefaultModelSettings(model_id="m1"))
    monkeypatch.setattr(default_model, "read_default_model", read)
    return read


@pytest.mark.asyncio
async def test_default_model_beats_registry_order(registry):
    result = await AssistantProviderClient().resolve()
    assert result.model == "deepseek-v4-1-flash"
    registry.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_choice_preserves_agent_selection(registry):
    result = await AssistantProviderClient().resolve(provider_id="p1", model="other")
    assert result.model == "other"
    registry.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_default_does_not_silently_change_provider(registry, monkeypatch):
    monkeypatch.setattr(ai_service, "get_model", AsyncMock(return_value=None))
    with pytest.raises(AssistantProviderError, match="default LLM is unavailable"):
        await AssistantProviderClient().resolve()


@pytest.mark.asyncio
async def test_embedding_cannot_be_selected_for_chat(registry, monkeypatch):
    monkeypatch.setattr(ai_service, "list_models", AsyncMock(return_value=[
        {"name": "vector", "type": "embedding", "is_active": True},
    ]))
    with pytest.raises(AssistantProviderError, match="selected model"):
        await AssistantProviderClient().resolve(provider_id="p1", model="vector")


@pytest.mark.asyncio
async def test_save_validates_before_writing(registry, monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(default_model.db, "execute_system", execute)
    monkeypatch.setattr(ai_service, "get_model", AsyncMock(return_value=None))
    with pytest.raises(ValueError):
        await default_model.save_default_model(default_model.DefaultModelSettings(model_id="gone"))
    execute.assert_not_awaited()
