"""Versioned features governed by shared Nova entities.

Feature View, Group, training-set and model-link control rows live in the PG
metadata store (``IntelligenceMetadataRepository``); materialized feature tables
stay in the engine under ``_NOVA_FEATURES``.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.database import db
from app.core.deps import get_current_user, require_role
from app.core.metadata_db import MetadataConflict, MetadataIntegrityViolation
from app.core.security import decrypt_password
from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.intelligence.access import can_manage
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.feature_pit import (
    FeatureRelation,
    TrainingRelation,
    compile_training_set,
)
from app.modules.intelligence.metadata_repository import intelligence_metadata, user_actor
from app.modules.query.service import query_service
from app.modules.users.router import ADMIN_ROLES

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser = Annotated[dict, Depends(require_role(*ADMIN_ROLES))]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
logger = logging.getLogger(__name__)


def _feature_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
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


VIEW_FIELDS = (
    "name", "id", "entity_id", "source_relation", "entity_keys", "event_timestamp",
    "feature_columns", "freshness_seconds", "ttl_seconds", "owner_name", "active_version",
    "status", "created_at", "updated_at",
)
VIEW_VERSION_FIELDS = (
    "view_name", "version", "relation_name", "definition", "status", "row_count", "created_at",
    "completed_at",
)
GROUP_FIELDS = (
    "name", "id", "entity_id", "owner_name", "active_version", "status", "created_at",
    "updated_at",
)
GROUP_VERSION_FIELDS = ("group_name", "version", "members", "status", "created_at", "activated_at")
BUILT = frozenset({"READY", "ACTIVE", "DEPRECATED"})


def _fields(row: dict[str, Any] | None, names: tuple[str, ...]) -> dict[str, Any] | None:
    """The API shape of a metadata row, without its revision and provenance."""
    return None if row is None else {name: row.get(name) for name in names}


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


async def _view_access(view: dict, user: dict) -> bool:
    columns = [*view["entity_keys"], view["event_timestamp"], *view["feature_columns"]]
    return await _source_access(view["source_relation"], columns, user)


class FeatureStoreService:
    """Feature control rows live in PG metadata; materialized tables stay in the engine.

    Source checks run as the caller against the engine between short metadata
    transactions; a write re-reads its row and moves it with the revision read
    before the check, so a concurrent change conflicts instead of being lost.
    """

    async def list_views(self, user: dict) -> list[dict]:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            rows = await repo.list_feature_views()
        visible = []
        for row in rows:
            if await _view_access(row, user):
                view = _fields(row, VIEW_FIELDS)
                assert view is not None
                visible.append(view)
        return visible

    async def list_groups(self, user: dict) -> list[dict]:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            names = [row["name"] for row in await repo.list_feature_groups()]
        visible = []
        for name in names:
            try:
                group, _, _ = await self._authorized_group(name, user)
                visible.append(group)
            except HTTPException:
                continue
        return visible

    @staticmethod
    async def _view(name: str, user: dict) -> dict | None:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            return await repo.get_feature_view(name)

    @staticmethod
    async def _view_version(name: str, version: int, user: dict) -> dict | None:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            return await repo.get_feature_view_version(name, version)

    @staticmethod
    async def _view_and_version(
        name: str, version: int, user: dict
    ) -> tuple[dict | None, dict | None]:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            return (
                await repo.get_feature_view(name),
                await repo.get_feature_view_version(name, version),
            )

    @staticmethod
    async def _group(name: str, user: dict) -> dict | None:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            return await repo.get_feature_group(name)

    @staticmethod
    async def _group_and_version(
        name: str, version: int | None, user: dict
    ) -> tuple[dict | None, dict | None]:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            group = await repo.get_feature_group(name)
            selected = version or (group or {}).get("active_version")
            row = await repo.get_feature_group_version(name, selected) if selected else None
            return group, row

    @staticmethod
    def _physical(view_id: str, version: int) -> str:
        compact = view_id.replace("-", "")
        if not re.fullmatch(r"[0-9a-f]{32}", compact) or version < 1:
            raise ValueError("Invalid Feature View identity")
        return f"`_NOVA_FEATURES`.`FV_{compact}_V{version}`"

    async def _authorized_view(self, name: str, user: dict) -> dict:
        view = await self._view(name, user)
        if not view or view["status"] == "DEPRECATED" or not await _view_access(view, user):
            raise HTTPException(status_code=404, detail="Feature View not found")
        public = _fields(view, VIEW_FIELDS)
        assert public is not None
        return public

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
        view_id = str(uuid4())
        definition = {**body.model_dump(), "entity_keys": keys}
        relation = self._physical(view_id, 1)
        try:
            # The building view and its first building version are the persisted
            # intent of the engine build, committed before the table exists.
            async with intelligence_metadata(user_actor(user)) as repo:
                if await repo.get_feature_view(body.name):
                    raise HTTPException(status_code=409, detail="Feature View already exists")
                await repo.create_feature_view(
                    {**definition, "id": view_id}, relation_name=relation
                )
        except MetadataConflict as exc:
            raise HTTPException(status_code=409, detail="Feature View already exists") from exc
        await self._materialize(body.name, view_id, 1, definition, user)
        await _audit("CREATE_VIEW", body.name, user)
        created = await self._view(body.name, user)
        assert created is not None
        public = _fields(created, VIEW_FIELDS)
        assert public is not None
        return public

    async def _materialize(
        self, name: str, view_id: str, version: int, definition: dict, user: dict
    ) -> dict:
        """Build the engine table of a committed BUILDING version and record the outcome.

        A failed build is recorded as terminal ``FAILED``: the version number and
        its physical table are never reused, so nothing is replayed. When the
        outcome cannot be recorded, the version stays ``BUILDING`` (the intent)
        for verification against the engine instead of a blind rebuild.
        """
        relation = self._physical(view_id, version)
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
        except Exception:
            await self._discard(relation)
            async with intelligence_metadata(user_actor(user)) as repo:
                await repo.finish_feature_view_version(name, version, row_count=None)
            raise
        async with intelligence_metadata(user_actor(user)) as repo:
            finished = await repo.finish_feature_view_version(name, version, row_count=count)
            if finished and version == 1:
                view = await repo.get_feature_view(name)
                assert view is not None
                await repo.activate_feature_view(
                    name, version, expected_revision=view["metadata_revision"]
                )
            row = await repo.get_feature_view_version(name, version) if finished else None
        if row is None:
            # The view was dropped while building: its table is no longer owned.
            await self._discard(relation)
            raise HTTPException(status_code=409, detail="Feature View changed during the build")
        return row

    @staticmethod
    async def _discard(relation: str) -> None:
        try:
            await db.execute_system(f"DROP TABLE IF EXISTS {relation}")
        except Exception:
            logger.warning("Feature table cleanup failed")

    async def activate_view(self, name: str, version: int, user: dict) -> dict:
        async with intelligence_metadata(user_actor(user)) as repo:
            view = await repo.get_feature_view(name)
            if not view or not can_manage(view["owner_name"], user):
                raise HTTPException(status_code=404, detail="Feature View not found")
            row = await repo.get_feature_view_version(name, version)
            if not row or row["status"] not in BUILT:
                raise HTTPException(status_code=409, detail="Feature version is not ready")
            activated = await repo.activate_feature_view(
                name, version, expected_revision=view["metadata_revision"]
            )
        await _audit("ACTIVATE_VIEW", name, user)
        public = _fields(activated, VIEW_FIELDS)
        assert public is not None
        return public

    async def refresh_view(self, name: str, user: dict) -> dict:
        view = await self._authorized_view(name, user)
        if not can_manage(view["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature View not found")
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
        async with intelligence_metadata(user_actor(user)) as repo:
            current = await repo.get_feature_view(name)
            if not current or current["id"] != view["id"]:
                raise HTTPException(status_code=404, detail="Feature View not found")
            latest = await repo.latest_feature_view_version(name)
            number = latest + 1
            await repo.add_feature_view_version(
                name, self._physical(view["id"], number), definition, expected_latest=latest
            )
        created = await self._materialize(name, view["id"], number, definition, user)
        await _audit("REFRESH_VIEW", name, user)
        public = _fields(created, VIEW_VERSION_FIELDS)
        assert public is not None
        return public

    async def drop_view(self, name: str, user: dict) -> None:
        try:
            async with intelligence_metadata(user_actor(user)) as repo:
                view = await repo.get_feature_view(name)
                if not view or not can_manage(view["owner_name"], user):
                    raise HTTPException(status_code=404, detail="Feature View not found")
                versions = [row["version"] for row in await repo.feature_view_versions(name)]
                await repo.drop_feature_view(name, expected_revision=view["metadata_revision"])
        except MetadataIntegrityViolation as exc:
            raise HTTPException(
                status_code=409, detail="Feature View is pinned by a group"
            ) from exc
        # The metadata is gone first, so no group can pin a table being dropped;
        # DROP IF EXISTS is idempotent cleanup of tables nothing references.
        for version in versions:
            await self._discard(self._physical(view["id"], version))
        await _audit("DROP_VIEW", name, user)

    async def create_group(self, body: FeatureGroupDefinition, user: dict) -> dict:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            if await repo.get_feature_group(body.name):
                raise HTTPException(status_code=409, detail="Feature Group already exists")
        members = await self._validate_members(body.entity_id, body.members, user)
        try:
            async with intelligence_metadata(user_actor(user)) as repo:
                if await repo.get_feature_group(body.name):
                    raise HTTPException(status_code=409, detail="Feature Group already exists")
                created = await repo.create_feature_group(
                    name=body.name, group_id=str(uuid4()), entity_id=body.entity_id,
                    members=members,
                )
        except MetadataConflict as exc:
            raise HTTPException(status_code=409, detail="Feature Group already exists") from exc
        except MetadataIntegrityViolation as exc:
            raise HTTPException(
                status_code=422, detail="Feature View version is unavailable"
            ) from exc
        await _audit("CREATE_GROUP", body.name, user)
        public = _fields(created, GROUP_FIELDS)
        assert public is not None
        return public

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
            version = await self._view_version(member.view_name, member.version, user)
            if view["entity_id"] != entity_id or not version or version["status"] not in BUILT:
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
        group, _ = await self._group_and_version(name, None, user)
        if not group or not can_manage(group["owner_name"], user):
            raise HTTPException(status_code=404, detail="Feature Group not found")
        selected = await self._validate_members(group["entity_id"], members, user)
        try:
            async with intelligence_metadata(user_actor(user)) as repo:
                current = await repo.get_feature_group(name)
                if not current or current["id"] != group["id"]:
                    raise HTTPException(status_code=404, detail="Feature Group not found")
                latest = await repo.latest_feature_group_version(name)
                created = await repo.add_feature_group_version(
                    name, selected, expected_latest=latest
                )
        except MetadataIntegrityViolation as exc:
            raise HTTPException(
                status_code=422, detail="Feature View version is unavailable"
            ) from exc
        await _audit("ALTER_GROUP", name, user)
        public = _fields(created, GROUP_VERSION_FIELDS)
        assert public is not None
        return public

    async def activate_group(self, name: str, version: int, user: dict) -> dict:
        group, row = await self._group_and_version(name, version, user)
        if not group or not can_manage(group["owner_name"], user) or not row:
            raise HTTPException(status_code=404, detail="Feature Group not found")
        await self._validate_members(
            group["entity_id"], [FeatureGroupMember(**item) for item in row["members"]], user
        )
        async with intelligence_metadata(user_actor(user)) as repo:
            current = await repo.get_feature_group(name)
            if not current or current["id"] != group["id"]:
                raise HTTPException(status_code=404, detail="Feature Group not found")
            # The revision read before the member checks: any change since conflicts.
            activated = await repo.activate_feature_group(
                name, version, expected_revision=group["metadata_revision"]
            )
        await _audit("ACTIVATE_GROUP", name, user)
        public = _fields(activated, GROUP_FIELDS)
        assert public is not None
        return public

    async def _authorized_group(self, name: str, user: dict, version: int | None = None):
        group, row = await self._group_and_version(name, version, user)
        if not group or group["status"] == "DEPRECATED":
            raise HTTPException(status_code=404, detail="Feature Group not found")
        if not row:
            raise HTTPException(status_code=404, detail="Feature Group version not found")
        if not await entity_registry.get(group["entity_id"], user):
            raise HTTPException(status_code=404, detail="Feature Group not found")
        members = await self._validate_members(
            group["entity_id"], [FeatureGroupMember(**item) for item in row["members"]], user
        )
        return _fields(group, GROUP_FIELDS), _fields(row, GROUP_VERSION_FIELDS), members

    async def lookup(self, name: str, body: FeatureLookup, user: dict) -> dict:
        group, group_version, members = await self._authorized_group(name, user, body.version)
        entity = await entity_registry.get(group["entity_id"], user)
        assert entity is not None
        if set(body.entity_key) != set(entity.key_columns):
            raise HTTPException(status_code=422, detail="Entity key does not match registry")
        values: dict[str, Any] = {}
        times: list[datetime] = []
        freshness: dict[str, dict[str, str]] = {}
        for member in members:
            view, version = await self._view_and_version(
                member["view_name"], member["version"], user
            )
            if view is None or version is None:
                raise HTTPException(status_code=404, detail="Feature View version unavailable")
            definition = version["definition"]
            columns = definition["feature_columns"]
            timestamp = definition["event_timestamp"]
            conditions = [
                f"{quote_identifier(key)}={_literal(body.entity_key[key])}"
                for key in entity.key_columns
            ]
            if body.as_of is not None:
                conditions.append(f"{quote_identifier(timestamp)}<={_literal(body.as_of)}")
            relation = quote_source(definition["source_relation"])
            sql = (
                "SELECT "
                + ",".join(quote_identifier(column) for column in [timestamp, *columns])
                + f" FROM {relation} WHERE "
                + " AND ".join(conditions)
                + f" ORDER BY {quote_identifier(timestamp)} DESC LIMIT 1"
            )
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
            if not rows:
                continue
            row = rows[0]
            observed = row[0]
            if isinstance(observed, datetime) and observed.tzinfo is None:
                observed = observed.replace(tzinfo=ZoneInfo(settings.NOVA_TIMEZONE))
            times.append(observed)
            values.update({
                column: _feature_value(value)
                for column, value in zip(columns, row[1:], strict=True)
            })
            age = (datetime.now(UTC) - observed.astimezone(UTC)).total_seconds()
            freshness[member["view_name"]] = {
                "as_of": observed.isoformat(),
                "status": "FRESH" if age <= view["freshness_seconds"] else "STALE",
            }
        if not values:
            raise HTTPException(status_code=404, detail="Features not found for entity")
        observed = max(times)
        await _audit("LOOKUP", name, user)
        return {
            "group": name,
            "version": group_version["version"],
            "values": values,
            "as_of": observed.isoformat(),
            "freshness": freshness,
            "source": "offline",
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
            version = await self._view_version(member["view_name"], member["version"], user)
            if version is None:
                raise HTTPException(status_code=404, detail="Feature View version unavailable")
            definition = version["definition"]
            relation = FeatureRelation(
                definition["source_relation"],
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
        try:
            async with intelligence_metadata(user_actor(user)) as repo:
                await repo.record_training_set(
                    {
                        "id": training_id,
                        "group_name": name,
                        "group_version": group_version["version"],
                        "label_relation": body.label_relation,
                        "generated_sql": sql,
                        "lineage": lineage,
                    }
                )
        except MetadataIntegrityViolation as exc:
            # Only an activated (or retired) group version can be trained from.
            raise HTTPException(
                status_code=409, detail="Feature Group version is not active"
            ) from exc
        await _audit("CREATE_TRAINING_SET", name, user)
        return {"id": training_id, "sql": sql, "lineage": lineage, "preview": preview}

    async def train(self, name: str, body: FeatureTrainRequest, user: dict) -> dict:
        from app.modules.ml_engine.service import ml_engine_service

        _, _, members = await self._authorized_group(name, user, body.group_version)
        feature_columns = []
        for member in members:
            version = await self._view_version(member["view_name"], member["version"], user)
            if version is None:
                raise HTTPException(status_code=404, detail="Feature View version unavailable")
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
        from app.modules.ml_engine.spec import MLError, TrainingTimeout

        try:
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
                security_context_version=user.get("security_context_version", 1),
                session_id=user.get("session_id"),
            )
        except TrainingTimeout as exc:
            # A spent ML budget is the caller's answer, not an unexpected server fault.
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except MLError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # The committed training set is the intent; the link records the trained model.
        async with intelligence_metadata(user_actor(user)) as repo:
            await repo.link_model(
                {
                    "id": str(uuid4()),
                    "model_id": model["model_id"],
                    "model_version": model["version"],
                    "training_set_id": training["id"],
                    "group_name": name,
                    "group_version": training["lineage"]["group_version"],
                    "lineage": training["lineage"],
                }
            )
        await _audit("TRAIN_MODEL", name, user)
        return {"model": model, "training_set": training}

    async def drop_group(self, name: str, user: dict) -> None:
        async with intelligence_metadata(user_actor(user)) as repo:
            group = await repo.get_feature_group(name)
            if not group or not can_manage(group["owner_name"], user):
                raise HTTPException(status_code=404, detail="Feature Group not found")
            await repo.drop_feature_group(name, expected_revision=group["metadata_revision"])
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


@router.post("/groups/{name}/train", response_class=SanitizingJSONResponse)
async def train_from_feature_group(name: str, body: FeatureTrainRequest, user: AdminUser):
    return await feature_store.train(name, body, user)


@router.delete("/groups/{name}", status_code=204)
async def drop_feature_group(name: str, user: AdminUser):
    await feature_store.drop_group(name, user)
