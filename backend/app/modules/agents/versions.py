"""Immutable agent configuration snapshots; publication uses a guarded row update."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.core.database import db
from app.modules.agents.schemas import AgentCreateRequest, AgentUpdateRequest
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools.redaction import redact_row

VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_VERSIONS (
    version_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    configuration JSON NOT NULL,
    label VARCHAR(256) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(version_id)
DISTRIBUTED BY HASH(version_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class AgentDraftRequest(BaseModel):
    configuration: AgentUpdateRequest
    label: str = Field(default="Saved draft", min_length=1, max_length=256)
    expected_revision: str | None = Field(default=None, max_length=64)


class AgentPublishRequest(BaseModel):
    expected_revision: str | None = Field(default=None, max_length=64)


def configuration(agent: dict) -> dict:
    fields = {key: value for key, value in agent.items() if key in AgentCreateRequest.model_fields}
    fields.pop("semantic_model_id", None)
    fields.pop("semantic_model_ids", None)
    fields["resource_bindings"] = fields.get("resource_bindings") or {}
    return AgentCreateRequest.model_validate(fields).model_dump(
        exclude={"semantic_model_id", "semantic_model_ids"}
    )


def fingerprint(agent: dict) -> str:
    return hashlib.sha256(json.dumps(configuration(agent), sort_keys=True).encode()).hexdigest()


def revision_id(agent: dict) -> str:
    return (
        agent.get("config_revision")
        or hashlib.sha256(f"{agent['agent_id']}:{fingerprint(agent)}".encode()).hexdigest()
    )


class AgentVersions:
    async def get(self, agent_id: str, owner: str, version_id: str) -> dict | None:
        result = await db.execute_system(
            "SELECT version_id,configuration,label,created_at "
            "FROM NOVA_SYSTEM.CONFIG_AGENT_VERSIONS "
            "WHERE agent_id=%s AND owner_name=%s AND version_id=%s",
            [agent_id, owner, version_id],
        )
        return self._row(result["rows"][0]) if result["rows"] else None

    @staticmethod
    def _row(row: list) -> dict:
        return {
            "version_id": row[0],
            "configuration": json.loads(row[1]) if isinstance(row[1], str) else row[1],
            "label": row[2],
            "created_at": str(row[3]),
        }

    async def store(self, agent: dict, *, label: str, version_id: str | None = None) -> dict:
        try:
            snapshot = configuration(agent)
        except ValidationError as exc:
            raise HTTPException(422, "Agent configuration is incomplete or invalid") from exc
        encoded = json.dumps(snapshot, sort_keys=True)
        if (
            contains_credential_shape(encoded)
            or contains_credential_shape(label)
            or redact_row(["configuration"], [encoded])[0] != encoded
            or redact_row(["label"], [label])[0] != label
        ):
            raise HTTPException(422, "Remove credentials from agent configuration before saving")
        if len(encoded.encode()) > 262144:
            raise HTTPException(422, "Agent configuration exceeds the version size limit")
        version_id = version_id or str(uuid4())
        existing = await self.get(agent["agent_id"], agent["owner_name"], version_id)
        if existing:
            if existing["configuration"] != snapshot:
                raise HTTPException(409, "A saved version cannot be modified")
            return existing
        now = datetime.now(UTC).replace(tzinfo=None)
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_VERSIONS "
            "(version_id,agent_id,owner_name,configuration,label,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            [version_id, agent["agent_id"], agent["owner_name"], encoded, label, now],
        )
        return {
            "version_id": version_id,
            "configuration": snapshot,
            "label": label,
            "created_at": str(now),
        }

    async def list(self, agent_id: str, owner: str, offset: int, limit: int = 30) -> dict:
        result = await db.execute_system(
            "SELECT version_id,label,created_at FROM NOVA_SYSTEM.CONFIG_AGENT_VERSIONS "
            "WHERE agent_id=%s AND owner_name=%s "
            "ORDER BY created_at DESC,version_id DESC LIMIT %s OFFSET %s",
            [agent_id, owner, limit + 1, offset],
        )
        rows = result["rows"]
        return {
            "versions": [
                {"version_id": row[0], "label": row[1], "created_at": str(row[2])}
                for row in rows[:limit]
            ],
            "has_more": len(rows) > limit,
        }


agent_versions = AgentVersions()


async def validate_publication_dependencies(fields: dict, owner: str) -> None:
    from app.modules.agents.repository import agent_repository
    from app.modules.ai_ml.service import ai_service
    from app.modules.assistant.skill_registry import skill_library

    custom = {
        name.split(":", 1)[1]
        for name in fields.get("default_tools", [])
        if name.startswith("custom:")
    }
    if custom:
        available = {
            item["name"] for item in await agent_repository.list_custom_tools(owner_name=owner)
        }
        if custom - available:
            raise HTTPException(422, "A configured custom tool is unavailable")
    skills = set(fields.get("default_skills") or []) | set(fields.get("discoverable_skills") or [])
    personal = {name for name in skills if skill_library.get(name) is None}
    if personal:
        available = {item["name"] for item in await agent_repository.list_skills(owner_name=owner)}
        if personal - available:
            raise HTTPException(422, "A configured skill is unavailable")
    provider_id, model = fields.get("model_provider_id"), fields.get("model_name")
    if provider_id:
        provider = await ai_service.get_provider(provider_id)
        if not provider or not provider.get("is_active"):
            raise HTTPException(422, "The configured AI provider is unavailable")
        if model:
            models = await ai_service.list_models(provider_id)
            if not any(
                item.get("name") == model and item.get("is_active") and item.get("type") == "llm"
                for item in models
            ):
                raise HTTPException(422, "The configured response model is unavailable")
