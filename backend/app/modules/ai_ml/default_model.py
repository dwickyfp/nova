"""Persist the default LLM as a registry reference, without copying credentials."""

import json

from pydantic import BaseModel, ConfigDict, Field

from app.core.database import db
from app.modules.ai_ml.decision_settings import registered_model


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


async def save_default_model(value: DefaultModelSettings) -> DefaultModelSettings:
    if value.model_id:
        await registered_model(value.model_id, "llm")
    await db.execute_system(
        "INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES "
        "(user_name,pref_key,pref_value,updated_at) VALUES (%s,%s,%s,NOW())",
        ("__system__", "default_llm_model", json.dumps(value.model_dump())),
    )
    return value
