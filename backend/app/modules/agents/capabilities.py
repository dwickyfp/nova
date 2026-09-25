"""Compact, user-scoped delegation manifests for Studio Auto."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.core.database import db
from app.modules.agents.repository import _read_rows
from app.modules.assistant.skills import contains_credential_shape

CAPABILITIES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_AGENT_CAPABILITIES (
    agent_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    manifest JSON NOT NULL
) PRIMARY KEY(agent_id)
DISTRIBUTED BY HASH(agent_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


class CapabilityManifest(BaseModel):
    available_to_auto: bool = True
    delegation_description: str = Field(default="", max_length=1024)
    capability_tags: list[str] = Field(default_factory=list, max_length=24)
    owns: list[str] = Field(default_factory=list, max_length=32)
    good_for: list[str] = Field(default_factory=list, max_length=32)
    consult_when: list[str] = Field(default_factory=list, max_length=16)
    not_primary_for: list[str] = Field(default_factory=list, max_length=16)
    can_delegate: bool = False
    priority: int = Field(default=0, ge=-10, le=10)


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return {}


class CapabilityRepository:
    async def ensure_schema(self) -> None:
        await db.execute_system(CAPABILITIES_DDL)

    async def get(self, agent: dict[str, Any]) -> CapabilityManifest:
        def valid_row(row: list[Any]) -> bool:
            if (
                len(row) != 3
                or row[0] != agent["agent_id"]
                or row[1] != agent["owner_name"]
            ):
                return False
            try:
                CapabilityManifest.model_validate(_decode(row[2]))
                return True
            except (ValueError, TypeError):
                return False

        rows = await _read_rows(
            "SELECT agent_id, owner_name, manifest "
            "FROM NOVA_SYSTEM.CONFIG_AGENT_CAPABILITIES "
            "WHERE agent_id = %s AND owner_name = %s",
            [agent["agent_id"], agent["owner_name"]],
            valid_row,
        )
        if rows:
            return CapabilityManifest.model_validate(_decode(rows[0][2]))
        return CapabilityManifest(
            delegation_description=str(agent.get("description") or "")[:1024]
        )

    async def put(self, agent: dict[str, Any], manifest: CapabilityManifest) -> None:
        if contains_credential_shape(manifest.model_dump_json()):
            raise ValueError("Capability metadata cannot contain credentials")
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AGENT_CAPABILITIES "
            "(agent_id, owner_name, manifest) VALUES (%s, %s, %s)",
            [agent["agent_id"], agent["owner_name"], manifest.model_dump_json()],
        )

    async def delete(self, agent_id: str, owner_name: str) -> None:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_AGENT_CAPABILITIES "
            "WHERE agent_id = %s AND owner_name = %s",
            [agent_id, owner_name],
        )


capability_repository = CapabilityRepository()
