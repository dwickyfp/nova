"""System-wide, opt-in Studio routing settings; model credentials stay in the registry."""

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.database import db
from app.modules.ai_ml.service import ai_service


class DecisionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    decision_model_id: str | None = Field(default=None, max_length=64)
    light_model_id: str | None = Field(default=None, max_length=64)
    heavy_model_id: str | None = Field(default=None, max_length=64)
    min_probability: float = Field(default=0.85, ge=0.5, le=1)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    timeout_seconds: float = Field(default=4, ge=0.5, le=10)

    @model_validator(mode="after")
    def enabled_models(self) -> "DecisionSettings":
        if self.enabled and not all(
            (self.decision_model_id, self.light_model_id, self.heavy_model_id)
        ):
            raise ValueError("Select a decision model and both workload models before enabling.")
        return self


async def read_decision_settings() -> DecisionSettings:
    result = await db.execute_system(
        "SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "WHERE user_name = %s AND pref_key = %s",
        ("__system__", "studio_decision_mode"),
    )
    rows = result.get("rows") or []
    return DecisionSettings.model_validate_json(rows[0][0]) if rows else DecisionSettings()


async def registered_model(model_id: str, kind: str) -> tuple[dict, dict]:
    model = await ai_service.get_model(model_id)
    if not model or model.get("type") != kind or not model.get("is_active", True):
        raise ValueError(f"Selected {kind} model is unavailable.")
    provider = await ai_service.get_provider(model["provider_id"])
    if not provider or not provider.get("is_active", True):
        raise ValueError("Selected model's provider is unavailable.")
    if (provider.get("type") == "decision") != (kind == "decision"):
        raise ValueError("Selected model does not match its provider type.")
    if kind == "llm" and not provider.get("has_api_key"):
        raise ValueError("Selected LLM provider requires an API key.")
    return model, provider


async def save_decision_settings(value: DecisionSettings) -> DecisionSettings:
    for model_id, kind in (
        (value.decision_model_id, "decision"),
        (value.light_model_id, "llm"),
        (value.heavy_model_id, "llm"),
    ):
        if model_id and value.enabled:
            await registered_model(model_id, kind)
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name, pref_key, pref_value, updated_at) VALUES (%s, %s, %s, NOW())",
        ("__system__", "studio_decision_mode", json.dumps(value.model_dump())),
    )
    return value
