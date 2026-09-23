"""Shared business entity registry with caller-scoped source access."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.database import db
from app.core.deps import get_current_user
from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.assistant.skills import contains_credential_shape
from app.modules.intelligence.access import can_manage
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
    def _decode(row: list) -> EntityResponse:
        return EntityResponse(
            id=row[0],
            name=row[1],
            description=row[2] or "",
            catalog=row[3],
            database=row[4],
            schema_name=row[5],
            relation=row[6],
            key_columns=json.loads(row[7]) if isinstance(row[7], str) else row[7],
            owner=row[8],
            tags=json.loads(row[9]) if isinstance(row[9], str) else (row[9] or []),
            status=row[10],
            created_at=str(row[11]) if row[11] else None,
            updated_at=str(row[12]) if row[12] else None,
        )

    async def list(self, user: dict) -> list[EntityResponse]:
        result = await db.execute_system(
            "SELECT id,name,description,catalog_name,database_name,schema_name,"
            "relation_name,key_columns,owner_name,tags,status,created_at,updated_at "
            "FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE status='ACTIVE' ORDER BY name"
        )
        visible = []
        for row in result["rows"]:
            entity = self._decode(row)
            if await self._authorized(user, entity.relation, entity.key_columns, entity.database):
                visible.append(entity)
        return visible

    async def get(self, entity_id: str, user: dict) -> EntityResponse | None:
        result = await db.execute_system(
            "SELECT id,name,description,catalog_name,database_name,schema_name,"
            "relation_name,key_columns,owner_name,tags,status,created_at,updated_at "
            "FROM NOVA_SYSTEM.CONFIG_ENTITIES WHERE id=%s AND status='ACTIVE'",
            [entity_id],
        )
        if not result["rows"]:
            return None
        entity = self._decode(result["rows"][0])
        return (
            entity
            if await self._authorized(user, entity.relation, entity.key_columns, entity.database)
            else None
        )

    async def create(self, body: EntityCreate, user: dict) -> EntityResponse:
        if not await self._authorized(user, body.relation, body.key_columns, body.database):
            raise HTTPException(status_code=403, detail="Source relation is unavailable")
        scope = [body.catalog, body.database, body.schema_name, body.name]
        existing = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_ENTITIES "
            "WHERE catalog_name=%s AND database_name=%s AND schema_name=%s AND name=%s",
            scope,
        )
        if existing["rows"]:
            raise HTTPException(status_code=409, detail="Entity already exists")
        entity_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_ENTITIES "
            "(id,name,description,catalog_name,database_name,schema_name,relation_name,"
            "key_columns,owner_name,tags,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',NOW(),NOW())",
            [
                entity_id,
                body.name,
                body.description,
                body.catalog,
                body.database,
                body.schema_name,
                body.relation,
                json.dumps(body.key_columns),
                user["username"],
                json.dumps(body.tags),
            ],
        )
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
        result = await self.get(entity_id, user)
        assert result is not None
        return result

    async def deprecate(self, entity_id: str, user: dict) -> bool:
        entity = await self.get(entity_id, user)
        if entity is None or not can_manage(entity.owner, user):
            return False
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_ENTITIES SET status='DEPRECATED', updated_at=NOW() "
            "WHERE id=%s",
            [entity_id],
        )
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
        return True


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
