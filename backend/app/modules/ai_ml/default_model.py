"""Persist the default LLM as a registry reference, without copying credentials."""

import json

from pydantic import BaseModel, ConfigDict, Field

from app.common.preference_store import SYSTEM_OWNER, read_value, settings_reader, write_values
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.service import ai_service

DEFAULT_MODEL_KEY = "default_llm_model"


class DefaultModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=64)


async def read_default_model(actor: SecurityContext | None = None) -> DefaultModelSettings:
    """The deployment default; any valid actor may read it, so a caller without
    one (provider selection outside a request) uses the settings reader."""
    stored = await read_value(actor or settings_reader(), SYSTEM_OWNER, DEFAULT_MODEL_KEY)
    return DefaultModelSettings.model_validate_json(stored) if stored else DefaultModelSettings()


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


async def save_default_model(
    value: DefaultModelSettings, actor: SecurityContext,
) -> DefaultModelSettings:
    """Store the default as an active administrator (enforced by RLS)."""
    if value.model_id:
        await registered_model(value.model_id, "llm")
    await write_values(actor, SYSTEM_OWNER, {DEFAULT_MODEL_KEY: json.dumps(value.model_dump())})
    return value
