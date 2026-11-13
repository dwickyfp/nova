"""Attachment references granted to collaboration participants, without copying bodies."""

from __future__ import annotations

import copy
import hashlib

from pydantic import Field

from app.modules.agents.metadata_repository import agent_metadata
from app.modules.agents.mission import workflow_scope
from app.modules.assistant.attachments import (
    MAX_FILES,
    MAX_TEXT_TOTAL_BYTES,
    MAX_TOTAL_BYTES,
)
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security
from app.modules.intelligence.contracts import Contract, Scope

RESOURCES_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_RESOURCES (
    resource_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    root_run_id VARCHAR(64) NOT NULL,
    message_id VARCHAR(64) NOT NULL,
    attachment_index INT NOT NULL,
    payload JSON NOT NULL
) PRIMARY KEY(resource_id)
DISTRIBUTED BY HASH(resource_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

GRANTS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.CONFIG_STUDIO_RESOURCE_GRANTS (
    resource_id VARCHAR(64) NOT NULL,
    participant_id VARCHAR(64) NOT NULL,
    owner_name VARCHAR(128) NOT NULL,
    role_name VARCHAR(128) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    security_version INT NOT NULL,
    thread_id VARCHAR(64) NOT NULL,
    root_run_id VARCHAR(64) NOT NULL,
    grantor_participant_id VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(resource_id,participant_id)
DISTRIBUTED BY HASH(resource_id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""

RESOURCE_DDLS = (RESOURCES_DDL, GRANTS_DDL)


class ResourceMetadata(Contract):
    resource_id: str
    message_id: str
    name: str = Field(max_length=255)
    media_type: str
    size_bytes: int = Field(ge=1, le=MAX_TOTAL_BYTES)
    digest: str = Field(min_length=64, max_length=64)
    attachment_index: int = Field(ge=0, lt=MAX_FILES)


def participant_id(turn: dict) -> str:
    return str((turn.get("payload") or {}).get("agent_session_id") or turn["run_id"])


def _digest(item: dict) -> str:
    return hashlib.sha256(str(item["content"]).encode()).hexdigest()


def check_participant(run: dict, user: dict) -> Scope:
    scope = workflow_scope(user)
    expected = (
        scope.principal,
        scope.active_role,
        scope.session_id,
        scope.security_context_version,
    )
    actual = tuple(
        run.get(k) for k in ("owner_name", "role_name", "session_id", "security_version")
    )
    if expected != actual:
        raise ValueError("Attachment security context changed")
    return scope


class ResourceDelegation:
    async def ensure_schema(self) -> None:
        """Only the migrator imports the retired collaboration resource history."""

    async def _metadata(self, resource_id: str, run: dict, scope: Scope) -> ResourceMetadata | None:
        root_id = run.get("root_run_id") or run["run_id"]
        async with agent_metadata(read_only=True) as repo:
            if (repo.actor.principal, repo.actor.active_role, repo.actor.session_id,
                repo.actor.security_context_version) != (
                scope.principal, scope.active_role, scope.session_id,
                scope.security_context_version,
            ):
                raise ValueError("Attachment security context changed")
            row = await repo.delegated_resource(resource_id, run["thread_id"], root_id)
        if row is None:
            return None
        value = row["payload"]
        metadata = (
            ResourceMetadata.model_validate_json(value)
            if isinstance(value, str)
            else ResourceMetadata.model_validate(value)
        )
        if metadata.resource_id != resource_id:
            raise ValueError("Attachment metadata does not match its reference")
        return metadata

    async def available(self, run: dict, user: dict) -> list[ResourceMetadata]:
        scope = check_participant(run, user)
        root_id = run.get("root_run_id") or run["run_id"]
        async with agent_metadata(read_only=True) as repo:
            grants = await repo.delegated_grants(participant_id(run), run["thread_id"], root_id)
        if len(grants) > MAX_FILES:
            raise ValueError("Participant attachment bound exceeded")
        result = []
        for row in grants:
            metadata = await self._metadata(row["resource_id"], run, scope)
            if metadata is None:
                raise ValueError("Attachment grant has no source")
            result.append(metadata)
        return sorted(result, key=lambda item: item.resource_id)

    async def load(self, run: dict, user: dict) -> tuple[list[dict], list[str]]:
        metadata = await self.available(run, user)
        attachments = []
        for resource in metadata:
            source = await assistant_repository.attachment_message(
                run["thread_id"],
                resource.message_id,
                user_name=user["username"],
                security_context=observation_context(session_security(user)),
            )
            if source is None or resource.attachment_index >= len(source["attachments"]):
                raise ValueError("Attachment source is unavailable")
            item = source["attachments"][resource.attachment_index]
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("content"), str)
                or _digest(item) != resource.digest
            ) or any(
                item.get(key) != getattr(resource, key)
                for key in ("name", "media_type", "size_bytes")
            ):
                raise ValueError("Attachment source changed after delegation")
            attachments.append(copy.deepcopy(item))
        if (
            sum(item["size_bytes"] for item in attachments) > MAX_TOTAL_BYTES
            or sum(
                len(item["content"].encode())
                for item in attachments
                if not item["media_type"].startswith("image/")
            )
            > MAX_TEXT_TOTAL_BYTES
        ):
            raise ValueError("Participant attachments exceed the input budget")
        return attachments, [r.resource_id for r in metadata]


resource_delegation = ResourceDelegation()
