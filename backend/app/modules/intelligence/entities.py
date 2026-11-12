"""Shared business entity registry with caller-scoped source access.

Entity rows live in the PG metadata store; whether a caller may see one stays
the engine's source check, run as the caller.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.deps import get_current_user
from app.core.metadata_db import MetadataConflict
from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.assistant.skills import contains_credential_shape
from app.modules.intelligence.access import can_manage
from app.modules.intelligence.metadata_repository import intelligence_metadata, user_actor
from app.modules.query.service import query_service

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
_SECRET_ASSIGNMENT = re.compile(r"\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*\S+", re.I)


class EntityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    description: str = Field(default="", max_length=4096)
    catalog: str = Field(default="default_catalog", max_length=128)
    database: str = Field(min_length=1, max_length=128)
    schema_name: str = Field(default="", max_length=128)
    relation: str = Field(min_length=1, max_length=512)
    key_columns: list[str] = Field(min_length=1, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("catalog", "database", "schema_name")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if value:
            quote_identifier(value)
        return value

    @field_validator("relation")
    @classmethod
    def validate_relation(cls, value: str) -> str:
        quote_source(value)
        if len(value.split(".")) < 2:
            raise ValueError("Entity relation must include a database")
        return value

    @field_validator("key_columns")
    @classmethod
    def validate_keys(cls, value: list[str]) -> list[str]:
        for column in value:
            quote_identifier(column)
        if len(value) != len({column.casefold() for column in value}):
            raise ValueError("Entity keys must be unique")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        if any(not tag.strip() or len(tag) > 128 for tag in value):
            raise ValueError("Entity tags must contain 1 to 128 characters")
        if len(value) != len({tag.casefold() for tag in value}):
            raise ValueError("Entity tags must be unique")
        return value

    @model_validator(mode="after")
    def validate_metadata(self) -> EntityCreate:
        parts = self.relation.split(".")
        if parts[-2] != self.database or (len(parts) == 3 and parts[0] != self.catalog):
            raise ValueError("Entity relation must match catalog and database")
        text = " ".join((self.description, *self.tags))
        if contains_credential_shape(text) or _SECRET_ASSIGNMENT.search(text):
            raise ValueError("Entity metadata must not contain credentials")
        return self


class EntityResponse(BaseModel):
    id: str
    name: str
    description: str
    catalog: str
    database: str
    schema_name: str
    relation: str
    key_columns: list[str]
    owner: str
    tags: list[str]
    status: Literal["ACTIVE", "DEPRECATED"]
    created_at: str | None = None
    updated_at: str | None = None


class EntityRegistry:
    @staticmethod
    async def _authorized(user: dict, relation: str, keys: list[str], database: str) -> bool:
        columns = ", ".join(quote_identifier(key) for key in keys)
        try:
            result = await query_service.execute(
                sql=f"SELECT {columns} FROM {quote_source(relation)} WHERE 1 = 0",
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                database=database,
                role=user.get("active_role"),
                session_id=user.get("session_id"),
                max_rows=0,
            )
            return not result.error
        except Exception:
            return False

    @staticmethod
    def _decode(row: dict) -> EntityResponse:
        return EntityResponse(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            catalog=row["catalog_name"],
            database=row["database_name"],
            schema_name=row["schema_name"],
            relation=row["relation_name"],
            key_columns=list(row["key_columns"]),
            owner=row["owner_name"],
            tags=list(row["tags"] or []),
            status=row["status"],
            created_at=str(row["created_at"]) if row["created_at"] else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        )

    async def list(self, user: dict) -> list[EntityResponse]:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            rows = await repo.list_entities()
        visible = []
        for row in rows:
            entity = self._decode(row)
            if await self._authorized(user, entity.relation, entity.key_columns, entity.database):
                visible.append(entity)
        return visible

    async def get(self, entity_id: str, user: dict) -> EntityResponse | None:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            row = await repo.get_entity(entity_id)
        if row is None:
            return None
        entity = self._decode(row)
        return (
            entity
            if await self._authorized(user, entity.relation, entity.key_columns, entity.database)
            else None
        )

    async def create(self, body: EntityCreate, user: dict) -> EntityResponse:
        if not await self._authorized(user, body.relation, body.key_columns, body.database):
            raise HTTPException(status_code=403, detail="Source relation is unavailable")
        entity_id = str(uuid4())
        try:
            # The scoped name is the primary key: a taken name conflicts in PG.
            async with intelligence_metadata(user_actor(user)) as repo:
                await repo.create_entity(
                    {
                        "id": entity_id,
                        "name": body.name,
                        "description": body.description,
                        "catalog": body.catalog,
                        "database": body.database,
                        "schema": body.schema_name,
                        "relation": body.relation,
                        "key_columns": body.key_columns,
                        "tags": body.tags,
                    }
                )
        except MetadataConflict as exc:
            raise HTTPException(status_code=409, detail="Entity already exists") from exc
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user["username"],
            action="CREATE",
            object_type="ENTITY",
            object_name=body.name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            database_name=body.database,
            schema_name=body.schema_name,
        )
        await _relink(entity_id)
        result = await self.get(entity_id, user)
        assert result is not None
        return result

    async def deprecate(self, entity_id: str, user: dict) -> bool:
        entity = await self.get(entity_id, user)
        if entity is None or not can_manage(entity.owner, user):
            return False
        async with intelligence_metadata(user_actor(user)) as repo:
            row = await repo.get_entity(entity_id)
            if row is None:
                return False
            await repo.deprecate_entity(entity_id, expected_revision=row["metadata_revision"])
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user["username"],
            action="DEPRECATE",
            object_type="ENTITY",
            object_name=entity.name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        await _relink(entity_id)
        return True


async def _relink(entity_id: str) -> None:
    """Point the catalog's securable link at this entity's current relation."""
    from app.modules.catalog.links import ENTITY, refresh_owner_links

    await refresh_owner_links(ENTITY, entity_id)


entity_registry = EntityRegistry()


@router.get("", response_model=list[EntityResponse], response_class=SanitizingJSONResponse)
async def list_entities(user: CurrentUser):
    return await entity_registry.list(user)


@router.post(
    "", response_model=EntityResponse, status_code=201, response_class=SanitizingJSONResponse
)
async def create_entity(body: EntityCreate, user: CurrentUser):
    return await entity_registry.create(body, user)


@router.get("/{entity_id}", response_model=EntityResponse, response_class=SanitizingJSONResponse)
async def get_entity(entity_id: str, user: CurrentUser):
    result = await entity_registry.get(entity_id, user)
    if result is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@router.delete("/{entity_id}", status_code=204)
async def deprecate_entity(entity_id: str, user: CurrentUser):
    if not await entity_registry.deprecate(entity_id, user):
        raise HTTPException(status_code=404, detail="Entity not found")
