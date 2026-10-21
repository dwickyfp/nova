"""Persist the default LLM as a registry reference, without copying credentials."""

import json

from pydantic import BaseModel, ConfigDict, Field

from app.core.database import db
from app.modules.ai_ml.service import ai_service


class DefaultModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=64)


async def read_default_model() -> DefaultModelSettings:
    result = await db.execute_system(
        "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "WHERE user_name=%s AND pref_key=%s",
        ("__system__", "default_llm_model"),
    )
    rows = result.get("rows") or []
    return DefaultModelSettings.model_validate_json(rows[0][0]) if rows else DefaultModelSettings()


async def registered_model(model_id: str, kind: str) -> tuple[dict, dict]:
    model = await ai_service.get_model(model_id)
    if not model or model.get("type") != kind or not model.get("is_active", True):
        raise ValueError(f"Selected {kind} model is unavailable.")
    provider = await ai_service.get_provider(model["provider_id"])
    if not provider or not provider.get("is_active", True):
        raise ValueError("Selected model's provider is unavailable.")
    if kind == "llm" and not provider.get("has_api_key"):
        raise ValueError("Selected LLM provider requires an API key.")
    return model, provider


async def save_default_model(value: DefaultModelSettings) -> DefaultModelSettings:
    if value.model_id:
        await registered_model(value.model_id, "llm")
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name,pref_key,pref_value,updated_at) VALUES (%s,%s,%s,NOW())",
        ("__system__", "default_llm_model", json.dumps(value.model_dump())),
    )
    return value
