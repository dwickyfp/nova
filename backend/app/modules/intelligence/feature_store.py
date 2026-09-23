"""Versioned offline and online features governed by shared Nova entities."""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from redis.exceptions import RedisError

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.database import db
from app.core.deps import get_current_user, require_role
from app.core.security import decrypt_password
from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.intelligence.access import can_manage
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.feature_pit import (
    FeatureRelation,
    TrainingRelation,
    compile_training_set,
)
from app.modules.intelligence.online_features import OnlineFeatureRecord, RedisOnlineFeatureStore
from app.modules.query.service import query_service
from app.modules.users.router import ADMIN_ROLES

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser = Annotated[dict, Depends(require_role(*ADMIN_ROLES))]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_ONLINE_LIMIT = 100_000
online_store = RedisOnlineFeatureStore()
logger = logging.getLogger(__name__)


def _online_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class FeatureViewDefinition(BaseModel):
    name: str = Field(pattern=_NAME.pattern)
    entity_id: str
    source_relation: str = Field(min_length=3, max_length=512)
    event_timestamp: str = Field(pattern=_NAME.pattern)
    feature_columns: list[str] = Field(min_length=1, max_length=128)
    freshness_seconds: int = Field(default=3600, ge=1, le=2_592_000)
    ttl_seconds: int = Field(default=86400, ge=1, le=2_592_000)

    @model_validator(mode="after")
    def validate_columns(self) -> FeatureViewDefinition:
        quote_source(self.source_relation)
        if len(self.source_relation.split(".")) not in (2, 3):
            raise ValueError("Feature source must include a database")
        if any(not _NAME.fullmatch(column) for column in self.feature_columns):
            raise ValueError("Feature columns must be simple identifiers")
        if len({column.casefold() for column in self.feature_columns}) != len(
            self.feature_columns
        ) or self.event_timestamp.casefold() in {
            column.casefold() for column in self.feature_columns
        }:
            raise ValueError("Feature columns must be distinct from event timestamp")
        return self


class FeatureGroupMember(BaseModel):
    view_name: str = Field(pattern=_NAME.pattern)
    version: int = Field(ge=1)


class FeatureGroupDefinition(BaseModel):
    name: str = Field(pattern=_NAME.pattern)
    entity_id: str
    members: list[FeatureGroupMember] = Field(min_length=1, max_length=16)


class FeatureLookup(BaseModel):
    entity_key: dict[str, str | int] = Field(min_length=1, max_length=8)
    as_of: datetime | None = None
    version: int | None = Field(default=None, ge=1)


class TrainingSetCreate(BaseModel):
    label_relation: str = Field(min_length=3, max_length=512)
    entity_keys: list[str] = Field(min_length=1, max_length=8)
    event_timestamp: str = Field(pattern=_NAME.pattern)
    label_columns: list[str] = Field(min_length=1, max_length=32)
    group_version: int | None = Field(default=None, ge=1)
    preview_rows: int = Field(default=0, ge=0, le=100)


class OnlineMaterializeRequest(BaseModel):
    entity_keys: list[dict[str, str | int]] = Field(min_length=1, max_length=100)
    group_version: int | None = Field(default=None, ge=1)


class FeatureTrainRequest(TrainingSetCreate):
    model_name: str = Field(min_length=1, max_length=256)
    model_type: str = Field(
        pattern=r"^(classification|regression|forecast|anomaly_detection|clustering)$"
    )
    target_column: str | None = Field(default=None, pattern=_NAME.pattern)
    algorithm: str = Field(default="auto", pattern=r"^[A-Za-z0-9_()=.-]+$")
    test_size: float = Field(default=0.2, ge=0, lt=1)

    @model_validator(mode="after")
    def validate_target(self) -> FeatureTrainRequest:
        if self.model_type in {"classification", "regression", "forecast"} and (
            not self.target_column or self.target_column not in self.label_columns
        ):
            raise ValueError("Target column must be one of the label columns")
        return self


def _record(result: dict, row: list) -> dict[str, Any]:
    return dict(zip(result["columns"], row, strict=True))


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _literal(value: str | int | datetime) -> str:
    encoded = base64.b64encode(str(value).encode()).decode()
    return f"FROM_BASE64('{encoded}')"


async def _audit(action: str, name: str, user: dict) -> None:
    await write_audit_log(
        event_type="FEATURE_STORE",
        user_name=user["username"],
        action=action,
        object_type="FEATURE_STORE",
        object_name=name,
        status="SUCCESS",
        session_id=user.get("session_id"),
        active_role=user.get("active_role"),
    )


async def _source_access(relation: str, columns: list[str], user: dict) -> bool:
    try:
        sql = (
            "SELECT "
            + ",".join(quote_identifier(column) for column in columns)
            + f" FROM {quote_source(relation)} WHERE 1=0"
        )
        result = await query_service.execute(
            sql=sql,
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            database=relation.split(".")[-2],
            role=user.get("active_role"),
            session_id=user.get("session_id"),
            max_rows=0,
        )
        return not result.error
    except Exception:
        return False


class FeatureStoreService:
    async def list_views(self, user: dict) -> list[dict]:
        result = await db.execute_system(
            "SELECT name FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEWS "
            "WHERE status<>'DEPRECATED' ORDER BY name"
        )
        visible = []
        for (name,) in result["rows"]:
            try:
                visible.append(await self._authorized_view(name, user))
            except HTTPException:
                continue
        return visible

    async def list_groups(self, user: dict) -> list[dict]:
        result = await db.execute_system(
            "SELECT name FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUPS "
            "WHERE status<>'DEPRECATED' ORDER BY name"
        )
        visible = []
        for (name,) in result["rows"]:
            try:
                group, _, _ = await self._authorized_group(name, user)
                visible.append(group)
            except HTTPException:
                continue
        return visible

    @staticmethod
    async def _view(name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT name,id,entity_id,source_relation,entity_keys,event_timestamp,"
            "feature_columns,freshness_seconds,ttl_seconds,owner_name,active_version,"
            "status,created_at,updated_at FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEWS WHERE name=%s",
            [name],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        row["entity_keys"] = _json(row["entity_keys"])
        row["feature_columns"] = _json(row["feature_columns"])
        return row

    @staticmethod
    async def _view_version(name: str, version: int) -> dict | None:
        result = await db.execute_system(
            "SELECT view_name,version,relation_name,definition,status,row_count,created_at,"
            "completed_at FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS "
            "WHERE view_name=%s AND version=%s",
            [name, version],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        row["definition"] = _json(row["definition"])
        return row

    @staticmethod
    async def _group(name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT name,id,entity_id,owner_name,active_version,status,created_at,"
            "updated_at FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUPS WHERE name=%s",
            [name],
        )
        return _record(result, result["rows"][0]) if result["rows"] else None

    @staticmethod
    async def _group_version(name: str, version: int) -> dict | None:
        result = await db.execute_system(
            "SELECT group_name,version,members,status,created_at,activated_at "
            "FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS "
            "WHERE group_name=%s AND version=%s",
            [name, version],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        row["members"] = _json(row["members"])
        return row

    @staticmethod
    def _physical(view_id: str, version: int) -> str:
        compact = view_id.replace("-", "")
        if not re.fullmatch(r"[0-9a-f]{32}", compact) or version < 1:
            raise ValueError("Invalid Feature View identity")
        return f"`_NOVA_FEATURES`.`FV_{compact}_V{version}`"

    async def _authorized_view(self, name: str, user: dict) -> dict:
        view = await self._view(name)
        if not view or view["status"] == "DEPRECATED":
            raise HTTPException(status_code=404, detail="Feature View not found")
        columns = [*view["entity_keys"], view["event_timestamp"], *view["feature_columns"]]
        if not await _source_access(view["source_relation"], columns, user):
            raise HTTPException(status_code=404, detail="Feature View not found")
        return view

    async def create_view(self, body: FeatureViewDefinition, user: dict) -> dict:
        entity = await entity_registry.get(body.entity_id, user)
        if not entity or entity.relation != body.source_relation:
            raise HTTPException(status_code=422, detail="Feature entity does not match source")
        keys = entity.key_columns
        if any(
            column.casefold() in {key.casefold() for key in keys} for column in body.feature_columns
        ):
            raise HTTPException(status_code=422, detail="Feature columns repeat entity keys")
        if not await _source_access(
            body.source_relation, [*keys, body.event_timestamp, *body.feature_columns], user
        ):
            raise HTTPException(status_code=403, detail="Feature source is unavailable")
        if await self._view(body.name):
            raise HTTPException(status_code=409, detail="Feature View already exists")
        view_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_VIEWS "
            "(name,id,entity_id,source_relation,entity_keys,event_timestamp,feature_columns,"
            "freshness_seconds,ttl_seconds,owner_name,active_version,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'BUILDING',NOW(),NOW())",
            [
                body.name,
                view_id,
                body.entity_id,
                body.source_relation,
                json.dumps(keys),
                body.event_timestamp,
                json.dumps(body.feature_columns),
                body.freshness_seconds,
                body.ttl_seconds,
                user["username"],
            ],
        )
        definition = {**body.model_dump(), "entity_keys": keys}
        await self._materialize(body.name, view_id, 1, definition)
        await _audit("CREATE_VIEW", body.name, user)
        created = await self._view(body.name)
        assert created is not None
        return created

    async def _materialize(self, name: str, view_id: str, version: int, definition: dict):
        relation = self._physical(view_id, version)
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS "
            "(view_name,version,relation_name,definition,status,row_count,created_at) "
            "VALUES (%s,%s,%s,%s,'BUILDING',0,NOW())",
            [name, version, relation, json.dumps(definition)],
        )
        keys = definition["entity_keys"]
        timestamp = definition["event_timestamp"]
        columns = [*keys, timestamp, *definition["feature_columns"]]
        try:
            await db.execute_system("CREATE DATABASE IF NOT EXISTS `_NOVA_FEATURES`")
            await db.execute_system(f"DROP TABLE IF EXISTS {relation}")
            await db.execute_system(
                f"CREATE TABLE {relation} DUPLICATE KEY("
                + ",".join(quote_identifier(column) for column in [*keys, timestamp])
                + ") DISTRIBUTED BY HASH("
                + ",".join(quote_identifier(column) for column in keys)
                + ') BUCKETS 1 PROPERTIES("replication_num"="1") AS SELECT '
                + ",".join(quote_identifier(column) for column in columns)
                + f" FROM {quote_source(definition['source_relation'])}"
            )
            result = await db.execute_system(f"SELECT COUNT(*) FROM {relation}")
            count = int(result["rows"][0][0])
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS "
                "SET status='READY',row_count=%s,completed_at=NOW() "
                "WHERE view_name=%s AND version=%s",
                [count, name, version],
            )
            if version == 1:
                await self.activate_view(name, version, None)
        except Exception:
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS "
                "SET status='FAILED',completed_at=NOW() WHERE view_name=%s AND version=%s",
                [name, version],
            )
            raise

    async def activate_view(self, name: str, version: int, user: dict | None):
        view = await self._view(name)
        if not view or (user and not can_manage(view["owner_name"], user)):
            raise HTTPException(status_code=404, detail="Feature View not found")
        row = await self._view_version(name, version)
        if not row or row["status"] not in {"READY", "ACTIVE", "DEPRECATED"}:
            raise HTTPException(status_code=409, detail="Feature version is not ready")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS SET status='DEPRECATED' "
            "WHERE view_name=%s AND status='ACTIVE'",
            [name],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS SET status='ACTIVE' "
            "WHERE view_name=%s AND version=%s",
            [name, version],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_VIEWS "
            "SET active_version=%s,status='ACTIVE',updated_at=NOW() WHERE name=%s",
            [version, name],
        )
        if user:
            await _audit("ACTIVATE_VIEW", name, user)
        return await self._view(name)

    async def refresh_view(self, name: str, user: dict) -> dict:
        view = await self._authorized_view(name, user)
        if not can_manage(view["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature View not found")
        result = await db.execute_system(
            "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS WHERE view_name=%s",
            [name],
        )
        number = int(result["rows"][0][0] or 0) + 1
        definition = {
            "name": name,
            "entity_id": view["entity_id"],
            "source_relation": view["source_relation"],
            "entity_keys": view["entity_keys"],
            "event_timestamp": view["event_timestamp"],
            "feature_columns": view["feature_columns"],
            "freshness_seconds": view["freshness_seconds"],
            "ttl_seconds": view["ttl_seconds"],
        }
        await self._materialize(name, view["id"], number, definition)
        await _audit("REFRESH_VIEW", name, user)
        created = await self._view_version(name, number)
        assert created is not None
        return created

    async def drop_view(self, name: str, user: dict) -> None:
        view = await self._view(name)
        if not view or not can_manage(view["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature View not found")
        referenced = await db.execute_system(
            "SELECT group_name,members FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS"
        )
        if any(
            member.get("view_name") == name
            for row in referenced["rows"]
            for member in (_json(row[1]) or [])
        ):
            raise HTTPException(status_code=409, detail="Feature View is pinned by a group")
        result = await db.execute_system(
            "SELECT version FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS WHERE view_name=%s",
            [name],
        )
        for row in result["rows"]:
            await db.execute_system(f"DROP TABLE IF EXISTS {self._physical(view['id'], row[0])}")
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS WHERE view_name=%s", [name]
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEWS WHERE name=%s", [name]
        )
        await _audit("DROP_VIEW", name, user)

    async def create_group(self, body: FeatureGroupDefinition, user: dict) -> dict:
        if await self._group(body.name):
            raise HTTPException(status_code=409, detail="Feature Group already exists")
        members = await self._validate_members(body.entity_id, body.members, user)
        group_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_GROUPS "
            "(name,id,entity_id,owner_name,active_version,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,1,'ACTIVE',NOW(),NOW())",
            [body.name, group_id, body.entity_id, user["username"]],
        )
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS "
            "(group_name,version,members,status,created_at,activated_at) "
            "VALUES (%s,1,%s,'ACTIVE',NOW(),NOW())",
            [body.name, json.dumps(members)],
        )
        await _audit("CREATE_GROUP", body.name, user)
        created = await self._group(body.name)
        assert created is not None
        return created

    async def _validate_members(
        self, entity_id: str, members: list[FeatureGroupMember], user: dict
    ) -> list[dict]:
        if len({member.view_name for member in members}) != len(members):
            raise HTTPException(status_code=422, detail="Feature Group members must be distinct")
        entity = await entity_registry.get(entity_id, user)
        if not entity:
            raise HTTPException(status_code=422, detail="Feature entity is unavailable")
        output = []
        feature_names: set[str] = set()
        for member in members:
            view = await self._authorized_view(member.view_name, user)
            version = await self._view_version(member.view_name, member.version)
            if (
                view["entity_id"] != entity_id
                or not version
                or version["status"] not in {"READY", "ACTIVE", "DEPRECATED"}
            ):
                raise HTTPException(status_code=422, detail="Feature View version is unavailable")
            definition = version["definition"]
            source_columns = [
                *definition["entity_keys"],
                definition["event_timestamp"],
                *definition["feature_columns"],
            ]
            if not await _source_access(definition["source_relation"], source_columns, user):
                raise HTTPException(status_code=404, detail="Feature View version unavailable")
            columns = definition["feature_columns"]
            if feature_names.intersection(column.casefold() for column in columns):
                raise HTTPException(status_code=422, detail="Feature Group columns overlap")
            feature_names.update(column.casefold() for column in columns)
            output.append(member.model_dump())
        return output

    async def add_group_version(
        self, name: str, members: list[FeatureGroupMember], user: dict
    ) -> dict:
        group = await self._group(name)
        if not group or not can_manage(group["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature Group not found")
        selected = await self._validate_members(group["entity_id"], members, user)
        result = await db.execute_system(
            "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS "
            "WHERE group_name=%s",
            [name],
        )
        number = int(result["rows"][0][0] or 0) + 1
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS "
            "(group_name,version,members,status,created_at) "
            "VALUES (%s,%s,%s,'DRAFT',NOW())",
            [name, number, json.dumps(selected)],
        )
        await _audit("ALTER_GROUP", name, user)
        created = await self._group_version(name, number)
        assert created is not None
        return created

    async def activate_group(self, name: str, version: int, user: dict) -> dict:
        group = await self._group(name)
        row = await self._group_version(name, version)
        if not group or not can_manage(group["owner_name"], user) or not row:
            raise HTTPException(status_code=404, detail="Feature Group not found")
        await self._validate_members(
            group["entity_id"], [FeatureGroupMember(**item) for item in row["members"]], user
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS SET status='DEPRECATED' "
            "WHERE group_name=%s AND status='ACTIVE'",
            [name],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS "
            "SET status='ACTIVE',activated_at=NOW() WHERE group_name=%s AND version=%s",
            [name, version],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_FEATURE_GROUPS SET active_version=%s,"
            "status='ACTIVE',updated_at=NOW() WHERE name=%s",
            [version, name],
        )
        await _audit("ACTIVATE_GROUP", name, user)
        activated = await self._group(name)
        assert activated is not None
        return activated

    async def _authorized_group(self, name: str, user: dict, version: int | None = None):
        group = await self._group(name)
        if not group or group["status"] == "DEPRECATED":
            raise HTTPException(status_code=404, detail="Feature Group not found")
        selected = version or group["active_version"]
        row = await self._group_version(name, selected) if selected else None
        if not row:
            raise HTTPException(status_code=404, detail="Feature Group version not found")
        if not await entity_registry.get(group["entity_id"], user):
            raise HTTPException(status_code=404, detail="Feature Group not found")
        members = await self._validate_members(
            group["entity_id"], [FeatureGroupMember(**item) for item in row["members"]], user
        )
        return group, row, members

    async def lookup(self, name: str, body: FeatureLookup, user: dict) -> dict:
        group, group_version, members = await self._authorized_group(name, user, body.version)
        entity = await entity_registry.get(group["entity_id"], user)
        assert entity is not None
        if set(body.entity_key) != set(entity.key_columns):
            raise HTTPException(status_code=422, detail="Entity key does not match registry")
        if body.as_of is None and not settings.RANGER_ENABLED:
            try:
                cached = await online_store.get(name, group_version["version"], body.entity_key)
            except (ConnectionError, TimeoutError, OSError, RedisError):
                logger.warning("Online Feature Store unavailable; using offline lookup")
                cached = None
            if cached:
                await _audit("LOOKUP", name, user)
                return {
                    "group": name,
                    "version": group_version["version"],
                    "values": cached["values"],
                    "as_of": cached["as_of"],
                    "source": "online",
                }
        values: dict[str, Any] = {}
        times: list[datetime] = []
        freshness: dict[str, dict[str, str]] = {}
        ttl_values: list[int] = []
        for member in members:
            view = await self._view(member["view_name"])
            version = await self._view_version(member["view_name"], member["version"])
            assert view is not None and version is not None
            definition = version["definition"]
            columns = definition["feature_columns"]
            timestamp = definition["event_timestamp"]
            conditions = [
                f"{quote_identifier(key)}={_literal(body.entity_key[key])}"
                for key in entity.key_columns
            ]
            if body.as_of is not None:
                conditions.append(f"{quote_identifier(timestamp)}<={_literal(body.as_of)}")
            relation = (
                quote_source(definition["source_relation"])
                if settings.RANGER_ENABLED
                else version["relation_name"]
            )
            sql = (
                "SELECT "
                + ",".join(quote_identifier(column) for column in [timestamp, *columns])
                + f" FROM {relation} WHERE "
                + " AND ".join(conditions)
                + f" ORDER BY {quote_identifier(timestamp)} DESC LIMIT 1"
            )
            if settings.RANGER_ENABLED:
                queried = await query_service.execute(
                    sql=sql,
                    username=user["username"],
                    encrypted_password=user["encrypted_password"],
                    database=definition["source_relation"].split(".")[-2],
                    role=user.get("active_role"),
                    session_id=user.get("session_id"),
                    max_rows=1,
                )
                rows = queried.rows if not queried.error else []
            else:
                rows = (await db.execute_system(sql))["rows"]
            if not rows:
                continue
            row = rows[0]
            observed = row[0]
            if isinstance(observed, datetime) and observed.tzinfo is None:
                observed = observed.replace(tzinfo=ZoneInfo(settings.NOVA_TIMEZONE))
            times.append(observed)
            values.update({
                column: _online_value(value)
                for column, value in zip(columns, row[1:], strict=True)
            })
            age = (datetime.now(UTC) - observed.astimezone(UTC)).total_seconds()
            freshness[member["view_name"]] = {
                "as_of": observed.isoformat(),
                "status": "FRESH" if age <= view["freshness_seconds"] else "STALE",
            }
            ttl_values.append(int(view["ttl_seconds"]))
        if not values:
            raise HTTPException(status_code=404, detail="Features not found for entity")
        observed = max(times)
        if body.as_of is None and not settings.RANGER_ENABLED:
            ttl = min(ttl_values)
            try:
                await online_store.publish(
                    OnlineFeatureRecord(
                        name, group_version["version"], body.entity_key, values, observed, ttl
                    )
                )
            except (ConnectionError, TimeoutError, OSError, RedisError):
                logger.warning("Online Feature Store unavailable; offline lookup succeeded")
        await _audit("LOOKUP", name, user)
        return {
            "group": name,
            "version": group_version["version"],
            "values": values,
            "as_of": observed.isoformat(),
            "freshness": freshness,
            "source": "offline" if body.as_of else "offline_fallback",
        }

    async def create_training_set(self, name: str, body: TrainingSetCreate, user: dict) -> dict:
        group, group_version, members = await self._authorized_group(name, user, body.group_version)
        entity = await entity_registry.get(group["entity_id"], user)
        assert entity is not None
        if body.entity_keys != entity.key_columns:
            raise HTTPException(
                status_code=422, detail="Training entity keys do not match registry"
            )
        if not await _source_access(
            body.label_relation,
            [*body.entity_keys, body.event_timestamp, *body.label_columns],
            user,
        ):
            raise HTTPException(status_code=403, detail="Label relation is unavailable")
        labels = TrainingRelation(
            body.label_relation,
            tuple(body.entity_keys),
            body.event_timestamp,
            tuple(body.label_columns),
        )
        features = []
        feature_lineage = []
        for member in members:
            version = await self._view_version(member["view_name"], member["version"])
            assert version is not None
            definition = version["definition"]
            relation = FeatureRelation(
                (
                    definition["source_relation"]
                    if settings.RANGER_ENABLED
                    else version["relation_name"]
                ),
                member["version"],
                tuple(body.entity_keys),
                definition["event_timestamp"],
                tuple(definition["feature_columns"]),
            )
            compile_training_set(labels, relation)
            features.append(relation)
            feature_lineage.append(
                {
                    "view_name": member["view_name"],
                    "version": member["version"],
                    "source_relation": definition["source_relation"],
                    "source_columns": definition["feature_columns"],
                    "event_timestamp": definition["event_timestamp"],
                    "transformation": "identity_projection",
                }
            )
        if len(features) == 1:
            sql = compile_training_set(labels, features[0]).sql
        else:
            projections = [
                f"l.{quote_identifier(column)} AS {quote_identifier(column)}"
                for column in (*body.entity_keys, body.event_timestamp, *body.label_columns)
            ]
            joins = []
            for index, feature in enumerate(features, 1):
                alias = f"f{index}"
                projections.extend(
                    f"{alias}.{quote_identifier(column)} AS {quote_identifier(column)}"
                    for column in feature.feature_columns
                )
                equality = " AND ".join(
                    f"l.{quote_identifier(key)}={alias}.{quote_identifier(key)}"
                    for key in body.entity_keys
                )
                joins.append(
                    f"ASOF LEFT JOIN {quote_source(feature.relation)} AS {alias} ON {equality} "
                    f"AND l.{quote_identifier(body.event_timestamp)}>="
                    f"{alias}.{quote_identifier(feature.event_timestamp)}"
                )
            sql = (
                "SELECT "
                + ",".join(projections)
                + f" FROM {quote_source(body.label_relation)} AS l "
                + " ".join(joins)
            )
        range_result = await query_service.execute(
            sql=(
                f"SELECT MIN({quote_identifier(body.event_timestamp)}),"
                f"MAX({quote_identifier(body.event_timestamp)}) "
                f"FROM {quote_source(body.label_relation)}"
            ),
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            database=body.label_relation.split(".")[-2],
            role=user.get("active_role"),
            session_id=user.get("session_id"),
            max_rows=1,
        )
        if range_result.error:
            raise HTTPException(status_code=403, detail="Label relation is unavailable")
        event_range = (
            [str(value) if value is not None else None for value in range_result.rows[0]]
            if range_result.rows
            else [None, None]
        )
        lineage = {
            "entity_id": group["entity_id"],
            "group": name,
            "group_version": group_version["version"],
            "views": [
                member.model_dump() if isinstance(member, FeatureGroupMember) else member
                for member in members
            ],
            "label_relation": body.label_relation,
            "label_columns": body.label_columns,
            "label_event_timestamp": body.event_timestamp,
            "training_event_range": event_range,
            "features": feature_lineage,
            "join": "ASOF_LESS_THAN_OR_EQUAL",
        }
        training_id = str(uuid4())
        preview = None
        if body.preview_rows:
            result = await query_service.execute(
                sql=f"{sql} LIMIT {body.preview_rows}",
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                database=body.label_relation.split(".")[-2],
                role=user.get("active_role"),
                session_id=user.get("session_id"),
                max_rows=body.preview_rows,
            )
            if result.error:
                raise HTTPException(status_code=422, detail="Training-set preview failed")
            preview = {"columns": result.columns, "rows": result.rows}
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_TRAINING_SETS "
            "(id,group_name,group_version,label_relation,generated_sql,lineage,"
            "owner_name,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
            [
                training_id,
                name,
                group_version["version"],
                body.label_relation,
                sql,
                json.dumps(lineage),
                user["username"],
            ],
        )
        await _audit("CREATE_TRAINING_SET", name, user)
        return {"id": training_id, "sql": sql, "lineage": lineage, "preview": preview}

    async def materialize_online(
        self, name: str, body: OnlineMaterializeRequest, user: dict
    ) -> dict:
        if settings.RANGER_ENABLED:
            raise HTTPException(
                status_code=409,
                detail="Online materialization is unavailable with row-level policies",
            )
        group, row, _ = await self._authorized_group(name, user, body.group_version)
        if not can_manage(group["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature Group not found")
        published = 0
        missing = 0
        failed = 0
        for entity_key in body.entity_keys:
            try:
                await self.lookup(
                    name,
                    FeatureLookup(entity_key=entity_key, version=row["version"]),
                    user,
                )
                cached = await online_store.get(name, row["version"], entity_key)
                if cached:
                    published += 1
                else:
                    failed += 1
            except HTTPException as exc:
                if exc.status_code == 404:
                    missing += 1
                else:
                    failed += 1
            except (ConnectionError, TimeoutError, OSError, RedisError):
                failed += 1
        await _audit("MATERIALIZE_ONLINE", name, user)
        return {
            "group": name,
            "version": row["version"],
            "published": published,
            "missing": missing,
            "failed": failed,
        }

    async def train(self, name: str, body: FeatureTrainRequest, user: dict) -> dict:
        from app.modules.ml_engine.service import ml_engine_service

        _, _, members = await self._authorized_group(name, user, body.group_version)
        feature_columns = []
        for member in members:
            version = await self._view_version(member["view_name"], member["version"])
            assert version is not None
            feature_columns.extend(version["definition"]["feature_columns"])
        training = await self.create_training_set(
            name,
            TrainingSetCreate(
                **body.model_dump(
                    exclude={"model_name", "model_type", "target_column", "algorithm", "test_size"}
                )
            ),
            user,
        )
        try:
            password = decrypt_password(user["encrypted_password"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="User connection unavailable") from exc
        model = await ml_engine_service.train_model(
            model_name=body.model_name,
            model_type=body.model_type,
            algorithm=body.algorithm,
            training_sql=training["sql"],
            target_column=body.target_column,
            feature_columns=feature_columns,
            hyperparameters=None,
            test_size=body.test_size,
            database_name=body.label_relation.split(".")[-2],
            created_by=user["username"],
            username=user["username"],
            password=password,
            role=user.get("active_role"),
        )
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_FEATURE_ML_LINKS "
            "(id,model_id,model_version,training_set_id,group_name,group_version,"
            "lineage,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
            [
                str(uuid4()),
                model["model_id"],
                model["version"],
                training["id"],
                name,
                training["lineage"]["group_version"],
                json.dumps(training["lineage"]),
            ],
        )
        await _audit("TRAIN_MODEL", name, user)
        return {"model": model, "training_set": training}

    async def drop_group(self, name: str, user: dict) -> None:
        group = await self._group(name)
        if not group or not can_manage(group["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature Group not found")
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS WHERE group_name=%s", [name]
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUPS WHERE name=%s", [name]
        )
        await _audit("DROP_GROUP", name, user)


feature_store = FeatureStoreService()


@router.get("/views", response_class=SanitizingJSONResponse)
async def list_feature_views(user: CurrentUser):
    return await feature_store.list_views(user)


@router.post("/views", status_code=201, response_class=SanitizingJSONResponse)
async def create_feature_view(body: FeatureViewDefinition, user: AdminUser):
    return await feature_store.create_view(body, user)


@router.get("/views/{name}", response_class=SanitizingJSONResponse)
async def describe_feature_view(name: str, user: CurrentUser):
    return await feature_store._authorized_view(name, user)


@router.post("/views/{name}/refresh", response_class=SanitizingJSONResponse)
async def refresh_feature_view(name: str, user: AdminUser):
    return await feature_store.refresh_view(name, user)


@router.post("/views/{name}/versions/{version}/activate", response_class=SanitizingJSONResponse)
async def activate_feature_view(name: str, version: int, user: AdminUser):
    return await feature_store.activate_view(name, version, user)


@router.delete("/views/{name}", status_code=204)
async def drop_feature_view(name: str, user: AdminUser):
    await feature_store.drop_view(name, user)


@router.post("/groups", status_code=201, response_class=SanitizingJSONResponse)
async def create_feature_group(body: FeatureGroupDefinition, user: AdminUser):
    return await feature_store.create_group(body, user)


@router.get("/groups", response_class=SanitizingJSONResponse)
async def list_feature_groups(user: CurrentUser):
    return await feature_store.list_groups(user)


@router.get("/groups/{name}", response_class=SanitizingJSONResponse)
async def describe_feature_group(name: str, user: CurrentUser):
    group, version, _ = await feature_store._authorized_group(name, user)
    return {**group, "active_definition": version}


@router.post("/groups/{name}/versions", status_code=201, response_class=SanitizingJSONResponse)
async def add_feature_group_version(name: str, members: list[FeatureGroupMember], user: AdminUser):
    return await feature_store.add_group_version(name, members, user)


@router.post("/groups/{name}/versions/{version}/activate", response_class=SanitizingJSONResponse)
async def activate_feature_group(name: str, version: int, user: AdminUser):
    return await feature_store.activate_group(name, version, user)


@router.post("/groups/{name}/lookup", response_class=SanitizingJSONResponse)
async def lookup_features(name: str, body: FeatureLookup, user: CurrentUser):
    return await feature_store.lookup(name, body, user)


@router.post("/groups/{name}/training-set", response_class=SanitizingJSONResponse)
async def create_training_set(name: str, body: TrainingSetCreate, user: CurrentUser):
    return await feature_store.create_training_set(name, body, user)


@router.post("/groups/{name}/materialize-online", response_class=SanitizingJSONResponse)
async def materialize_online(name: str, body: OnlineMaterializeRequest, user: AdminUser):
    return await feature_store.materialize_online(name, body, user)


@router.post("/groups/{name}/train", response_class=SanitizingJSONResponse)
async def train_from_feature_group(name: str, body: FeatureTrainRequest, user: AdminUser):
    return await feature_store.train(name, body, user)


@router.delete("/groups/{name}", status_code=204)
async def drop_feature_group(name: str, user: AdminUser):
    await feature_store.drop_group(name, user)
