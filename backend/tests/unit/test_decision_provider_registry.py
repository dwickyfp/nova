from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.modules.ai_ml.schemas import AIModelCreate, AIModelResponse, AIProviderCreate
from app.modules.ai_ml.service import AIService
from app.modules.assistant.provider import AssistantProviderClient, AssistantProviderError
from app.modules.llm_functions.service import LLMFunctionService


@pytest.mark.parametrize("endpoint", [
    "https://api.example.com/custom/decide/",
    "https://api.example.com/alpha/decisions?version=2",
])
async def test_provider_create_and_update_preserve_full_endpoint(monkeypatch, endpoint):
    service = AIService()
    cursor = AsyncMock()
    connection = MagicMock()
    connection.cursor.return_value.__aenter__.return_value = cursor
    monkeypatch.setattr(service, "_connect", AsyncMock(return_value=connection))
    data = AIProviderCreate(name="Decision", type="decision", endpoint=endpoint).model_dump()
    row = {"id": "p1", **data}
    monkeypatch.setattr(service, "get_provider", AsyncMock(return_value=row))

    await service.create_provider(data, "admin")
    assert cursor.execute.call_args.args[1][3] == endpoint
    await service.update_provider("p1", {"name": "Renamed"})
    assert cursor.execute.call_args.args[1][3] == endpoint
    assert cursor.execute.call_args.args[1][2] == "decision"


async def test_decision_connection_test_never_calls_an_inferred_endpoint(monkeypatch):
    client = MagicMock(side_effect=AssertionError("Unexpected network call"))
    monkeypatch.setattr("app.modules.ai_ml.service.guarded_async_client", client)
    result = await AIService().test_connection(
        "decision", "https://api.example.com/custom/decide/", None,
    )
    assert result["success"] is False
    assert "do not support model discovery" in result["message"]
    client.assert_not_called()


async def test_decision_model_can_be_registered_and_returned(monkeypatch):
    service = AIService()
    data = AIModelCreate(provider_id="p1", name="jev", type="decision").model_dump()
    cursor = AsyncMock()
    connection = MagicMock()
    connection.cursor.return_value.__aenter__.return_value = cursor
    monkeypatch.setattr(service, "_connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(service, "get_provider", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(service, "get_model", AsyncMock(return_value={"id": "m1", **data}))
    result = await service.create_model(data, "admin")
    assert AIModelResponse(**result).type == "decision"
    assert cursor.execute.call_args.args[1][4:6] == ("decision", None)


@pytest.mark.parametrize("extra", [{"max_tokens": 4096}, {"dimensions": 1536}])
def test_decision_model_rejects_unrelated_model_settings(extra):
    with pytest.raises(ValidationError):
        AIModelCreate(provider_id="p1", name="jev", type="decision", **extra)


async def test_assistant_skips_decision_provider_before_resolving_endpoint(monkeypatch):
    registry = "app.modules.assistant.provider.ai_service"
    monkeypatch.setattr(f"{registry}.list_providers", AsyncMock(return_value=[{
        "id": "p1", "type": "decision", "is_active": True, "has_api_key": True,
        "endpoint": "https://api.example.com/custom/decide/",
    }]))
    key = AsyncMock()
    monkeypatch.setattr(f"{registry}.get_provider_api_key", key)
    with pytest.raises(AssistantProviderError):
        await AssistantProviderClient().resolve(provider_id="p1")
    key.assert_not_awaited()


async def test_assistant_rejects_explicit_decision_model(monkeypatch):
    monkeypatch.setattr("app.modules.assistant.provider.ai_service.list_models", AsyncMock(
        return_value=[{"name": "jev", "type": "decision", "is_active": True}],
    ))
    with pytest.raises(AssistantProviderError):
        await AssistantProviderClient()._resolve_model("p1", "jev")


async def test_sql_function_does_not_build_chat_endpoint_for_decision(monkeypatch):
    service = LLMFunctionService()
    monkeypatch.setattr(service, "_get_provider", AsyncMock(return_value={
        "type": "decision", "endpoint": "https://api.example.com/custom/decide/",
    }))
    result = await service._register_single_udf("complete", {"provider_id": "p1"})
    assert result["registered"] is False
    assert "registration only" in result["error"]
