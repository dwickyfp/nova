"""Durable attachment references and explicit participant grants, without copying bodies."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from app.common.audit import write_audit_log
from app.core.config import settings
from app.core.database import db
from app.modules.agents.identity import participant_id, participant_path
from app.modules.agents.mission import SCOPE_SQL, scope_params, workflow_scope
from app.modules.assistant.attachments import (
    MAX_FILES,
    MAX_TEXT_TOTAL_BYTES,
    MAX_TOTAL_BYTES,
    attachment_prompt,
    provider_user_content,
)
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context, session_security
from app.modules.assistant.skills import contains_credential_shape
from app.modules.intelligence.contracts import Contract, Scope, fingerprint, utc_now
from app.observability.metrics import studio_operation

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


class ResourceGrantRequest(Contract):
    root_run_id: str = Field(min_length=1, max_length=64)
    grantor: str = Field(default="/root", max_length=512)
    target: str = Field(min_length=1, max_length=512)
    resource_refs: list[str] = Field(max_length=MAX_FILES)


def normalize_refs(refs: list[str] | None) -> list[str]:
    if refs is None:
        return []
    if (
        not isinstance(refs, list)
        or len(refs) > MAX_FILES
        or any(not isinstance(ref, str) or not ref or len(ref) > 64 for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        raise ValueError("Invalid attachment resource references")
    return sorted(refs)


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
        for ddl in RESOURCE_DDLS:
            await db.execute_system(ddl)

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(settings, "STUDIO_BUSINESS_WORKFLOW_ENABLED", False))

    async def register_root(self, root: dict, user: dict) -> list[ResourceMetadata]:
        if not self.enabled():
            return []
        scope = check_participant(root, user)
        if root.get("depth") != 0:
            raise ValueError("Only a root may register message attachments")
        message_id = (root.get("payload") or {}).get("user_message_id")
        if not message_id:
            return []
        source = await assistant_repository.attachment_message(
            root["thread_id"],
            message_id,
            user_name=scope.principal,
            security_context=observation_context(session_security(user)),
        )
        if source is None:
            raise ValueError("Attachment message is unavailable in this security context")
        attachments = source["attachments"]
        if len(attachments) > MAX_FILES:
            raise ValueError("Attachment source exceeds the file bound")
        resources = []
        for index, item in enumerate(attachments):
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                raise ValueError("Invalid persisted attachment")
            if not item.get("media_type", "").startswith("image/") and contains_credential_shape(
                item["content"]
            ):
                raise ValueError("Credential-bearing attachments cannot be delegated")
            resource_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "nova:attachment:"
                    + fingerprint([root["run_id"], scope.model_dump(), message_id, index]),
                )
            )
            metadata = ResourceMetadata(
                resource_id=resource_id,
                message_id=message_id,
                attachment_index=index,
                name=item["name"],
                media_type=item["media_type"],
                size_bytes=item["size_bytes"],
                digest=_digest(item),
            )
            prior = await self._metadata(resource_id, root, scope)
            if prior is not None and prior != metadata:
                raise ValueError("Attachment source changed after registration")
            if prior is None:
                await db.execute_system(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_RESOURCES (resource_id,owner_name,"
                    "role_name,session_id,security_version,thread_id,root_run_id,message_id,"
                    "attachment_index,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        resource_id,
                        *scope_params(scope),
                        root["thread_id"],
                        root["run_id"],
                        message_id,
                        index,
                        metadata.model_dump_json(),
                    ],
                )
            await self._grant(metadata, root, root, scope)
            resources.append(metadata)
        return resources

    async def _metadata(self, resource_id: str, run: dict, scope: Scope) -> ResourceMetadata | None:
        root_id = run.get("root_run_id") or run["run_id"]
        result = await db.execute_system(
            "SELECT payload FROM NOVA_SYSTEM.CONFIG_STUDIO_RESOURCES "
            f"WHERE resource_id=%s AND {SCOPE_SQL} AND thread_id=%s AND root_run_id=%s",
            [resource_id, *scope_params(scope), run["thread_id"], root_id],
        )
        if not result["rows"]:
            return None
        value = result["rows"][0][0]
        metadata = (
            ResourceMetadata.model_validate_json(value)
            if isinstance(value, str)
            else ResourceMetadata.model_validate(value)
        )
        if metadata.resource_id != resource_id:
            raise ValueError("Attachment metadata does not match its reference")
        return metadata

    async def available(self, run: dict, user: dict) -> list[ResourceMetadata]:
        if not self.enabled():
            return []
        scope = check_participant(run, user)
        root_id = run.get("root_run_id") or run["run_id"]
        grants = await db.execute_system(
            "SELECT resource_id FROM NOVA_SYSTEM.CONFIG_STUDIO_RESOURCE_GRANTS "
            f"WHERE participant_id=%s AND {SCOPE_SQL} AND thread_id=%s AND root_run_id=%s LIMIT 4",
            [participant_id(run), *scope_params(scope), run["thread_id"], root_id],
        )
        if len(grants["rows"]) > MAX_FILES:
            raise ValueError("Participant attachment bound exceeded")
        result = []
        for row in grants["rows"]:
            metadata = await self._metadata(row[0], run, scope)
            if metadata is None:
                raise ValueError("Attachment grant has no source")
            result.append(metadata)
        return sorted(result, key=lambda item: item.resource_id)

    async def _grant(
        self, metadata: ResourceMetadata, grantor: dict, recipient: dict, scope: Scope
    ) -> None:
        root_id = grantor.get("root_run_id") or grantor["run_id"]
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_STUDIO_RESOURCE_GRANTS (resource_id,participant_id,"
            "owner_name,role_name,session_id,security_version,thread_id,root_run_id,"
            "grantor_participant_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                metadata.resource_id,
                participant_id(recipient),
                *scope_params(scope),
                grantor["thread_id"],
                root_id,
                participant_id(grantor),
                utc_now().replace(tzinfo=None),
            ],
        )

    async def grant(self, grantor: dict, recipient: dict, refs: list[str], user: dict) -> list[str]:
        with studio_operation("resource", "grant"):
            try:
                return await self._grant_resources(grantor, recipient, refs, user)
            except ValueError:
                await self._audit(grantor, workflow_scope(user), "DENIED")
                raise

    async def _grant_resources(
        self, grantor: dict, recipient: dict, refs: list[str], user: dict
    ) -> list[str]:
        refs = normalize_refs(refs)
        if refs and not self.enabled():
            raise ValueError("Attachment resource delegation is unavailable")
        if not refs:
            return []
        scope = check_participant(grantor, user)
        check_participant(recipient, user)
        root_id = grantor.get("root_run_id") or grantor["run_id"]
        if (
            recipient.get("root_run_id") != root_id
            or recipient["thread_id"] != grantor["thread_id"]
        ):
            raise ValueError("Attachment delegation crosses a collaboration boundary")
        ancestor = participant_path(grantor).value.rstrip("/") + "/"
        if not participant_path(recipient).value.startswith(ancestor):
            raise ValueError("Attachment delegation requires a descendant participant")
        available = {r.resource_id: r for r in await self.available(grantor, user)}
        if any(ref not in available for ref in refs):
            raise ValueError("Cannot delegate an attachment unavailable to this participant")
        existing = {r.resource_id for r in await self.available(recipient, user)}
        if len(existing | set(refs)) > MAX_FILES:
            raise ValueError("Participant attachment bound exceeded")
        for ref in refs:
            if ref not in existing:
                await self._grant(available[ref], grantor, recipient, scope)
        await self._audit(grantor, scope, "SUCCESS")
        return refs

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

    @staticmethod
    async def _audit(run: dict, scope: Scope, status: str) -> None:
        await write_audit_log(
            event_type="STUDIO_RESOURCE",
            user_name=scope.principal,
            action="DELEGATE",
            object_type="AGENT_RUN",
            object_name=run["run_id"],
            status=status,
            active_role=scope.active_role,
            session_id=scope.session_id,
            security_context_version=scope.security_context_version,
        )


def reference_checkpoint(
    state: dict, *, prompt: str, attachments: list[dict], refs: list[str]
) -> dict:
    """Replace live input with references before writing a Smart checkpoint."""
    if not attachments:
        return copy.deepcopy(state)
    if len(attachments) != len(normalize_refs(refs)):
        raise ValueError("Checkpoint attachments require matching resource references")
    original = provider_user_content(attachment_prompt(prompt, attachments), attachments)
    secrets = []
    for item in attachments:
        body = item["content"]
        secrets.extend(
            (
                body,
                json.dumps(body, ensure_ascii=False)[1:-1],
                json.dumps(body, ensure_ascii=True)[1:-1],
            )
        )
        if item["media_type"].startswith("image/"):
            secrets.append(f"data:{item['media_type']};base64,{body}")
    secrets = sorted(set(secrets), key=len, reverse=True)

    def clean(value: Any):
        if isinstance(value, dict):
            if value.get("role") == "user" and value.get("content") == original:
                value = {**value, "content": {"nova_attachment_refs": refs, "task": prompt}}
            return {clean(k): clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        if isinstance(value, str):
            for body in secrets:
                if body:
                    value = value.replace(body, "[attachment content omitted]")
        return value

    return clean(state)


def restore_resource_checkpoint(
    state: dict | None, attachments: list[dict], refs: list[str]
) -> dict | None:
    if state is None:
        return None
    restored = copy.deepcopy(state)
    for message in restored.get("messages", []):
        content = message.get("content")
        if isinstance(content, dict) and "nova_attachment_refs" in content:
            if message.get("role") != "user" or content["nova_attachment_refs"] != refs:
                raise ValueError("Checkpoint attachment grants changed")
            message["content"] = provider_user_content(
                attachment_prompt(content["task"], attachments), attachments
            )
    return restored


resource_delegation = ResourceDelegation()
