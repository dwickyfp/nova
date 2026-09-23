"""Versioned AI Search indexes with caller-scoped retrieval and managed projections."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, Literal
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.config import settings
from app.core.database import db
from app.core.deps import get_current_user, require_role
from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.ai_ml.embeddings import ResolvedEmbeddingModel, embedding_service
from app.modules.intelligence.access import can_manage
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.retrieval import SearchCandidate, evaluate_relevance, fuse_results
from app.modules.intelligence.vector_backend import SearchProjection, vector_backend
from app.modules.query.service import query_service
from app.modules.users.router import ADMIN_ROLES

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser = Annotated[dict, Depends(require_role(*ADMIN_ROLES))]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BATCH_SIZE = 32
_MAX_SOURCE_ROWS = 1_000_000
_BUILD_TIMEOUT_SECONDS = 3600
logger = logging.getLogger(__name__)


class SearchIndexCreate(BaseModel):
    name: str = Field(pattern=_NAME.pattern)
    source_relation: str = Field(min_length=3, max_length=512)
    key_columns: list[str] = Field(min_length=1, max_length=8)
    content_columns: list[str] = Field(min_length=1, max_length=8)
    filter_columns: list[str] = Field(default_factory=list, max_length=16)
    model_alias: str | None = Field(default=None, max_length=128)
    entity_id: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> SearchIndexCreate:
        quote_source(self.source_relation)
        if len(self.source_relation.split(".")) not in (2, 3):
            raise ValueError("Search source must include a database")
        all_columns = (*self.key_columns, *self.content_columns, *self.filter_columns)
        if any(not _NAME.fullmatch(column) for column in all_columns):
            raise ValueError("Search columns must be simple identifiers")
        if len({column.casefold() for column in all_columns}) != len(all_columns):
            raise ValueError("Search columns must be distinct")
        if self.model_alias is not None and not self.model_alias.strip():
            raise ValueError("Embedding alias cannot be empty")
        return self


class SearchRebuild(BaseModel):
    model_alias: str | None = Field(default=None, max_length=128)


class SearchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    mode: Literal["LEXICAL", "SEMANTIC", "HYBRID"] = "HYBRID"
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=16)
    rrf_k: int = Field(default=60, ge=1, le=1000)


class SearchEvalCase(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    relevant: dict[str, int] = Field(min_length=1, max_length=1000)


class SearchEvalRequest(BaseModel):
    cases: list[SearchEvalCase] = Field(min_length=1, max_length=100)
    mode: Literal["LEXICAL", "SEMANTIC", "HYBRID"] = "HYBRID"
    top_k: int = Field(default=10, ge=1, le=100)


def _record(result: dict, row: list) -> dict[str, Any]:
    return dict(zip(result["columns"], row, strict=True))


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _source_key(values: list[Any]) -> str:
    key = json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str)
    if any(value is None for value in values) or len(key.encode()) > 512:
        raise ValueError("Source key is null or exceeds 512 bytes")
    return key


def _sql_filters(filters: dict, allowed: list[str]) -> tuple[str, list[str]]:
    if any(key not in allowed for key in filters):
        raise HTTPException(status_code=422, detail="Filter is not declared on this search index")
    clauses = []
    params = []
    for key, value in sorted(filters.items()):
        if not _NAME.fullmatch(key):
            raise HTTPException(status_code=422, detail="Invalid search filter")
        clauses.append(f"get_json_string(CAST(metadata AS VARCHAR), '$.{key}') = %s")
        params.append(str(value).lower() if isinstance(value, bool) else str(value))
    return " AND ".join(clauses), params


class SearchService:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._poller: asyncio.Task | None = None

    @asynccontextmanager
    async def _lock(self, name: str, *, wait_seconds: float = 0) -> AsyncIterator[None]:
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        key = f"nova:search:lock:{name}"
        token = uuid4().hex
        held = False
        renewal: asyncio.Task | None = None
        try:
            deadline = time.monotonic() + wait_seconds
            while not await client.set(key, token, nx=True, ex=90):
                if time.monotonic() >= deadline:
                    raise HTTPException(status_code=409, detail="Search index is busy")
                await asyncio.sleep(0.1)
            held = True
            owner = asyncio.current_task()

            async def renew() -> None:
                while True:
                    await asyncio.sleep(30)
                    try:
                        renewed = await client.eval(
                            "if redis.call('GET', KEYS[1]) == ARGV[1] "
                            "then return redis.call('EXPIRE', KEYS[1], 90) else return 0 end",
                            1,
                            key,
                            token,
                        )
                        if not renewed:
                            raise RuntimeError("Search index lock expired")
                    except Exception:
                        if owner:
                            owner.cancel()
                        raise

            renewal = asyncio.create_task(renew())
            yield
        finally:
            if renewal:
                renewal.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal
            try:
                if held:
                    await client.eval(
                        "if redis.call('GET', KEYS[1]) == ARGV[1] "
                        "then return redis.call('DEL', KEYS[1]) else return 0 end",
                        1,
                        key,
                        token,
                    )
            finally:
                await client.aclose()

    @staticmethod
    async def _source_access(definition: dict, user: dict) -> bool:
        columns = (
            *definition["key_columns"],
            *definition["content_columns"],
            *definition["filter_columns"],
        )
        source = definition["source_relation"]
        database = source.split(".")[-2]
        sql = (
            "SELECT "
            + ", ".join(quote_identifier(column) for column in columns)
            + f" FROM {quote_source(source)} WHERE 1=0"
        )
        try:
            result = await query_service.execute(
                sql=sql,
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
    async def _get(name: str) -> dict | None:
        result = await db.execute_system(
            "SELECT name,id,source_relation,key_columns,content_columns,filter_columns,"
            "entity_id,model_alias,owner_name,active_version,status,created_at,updated_at "
            "FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES WHERE name=%s",
            [name],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        for key in ("key_columns", "content_columns", "filter_columns"):
            row[key] = _json(row[key])
        for key in ("created_at", "updated_at"):
            row[key] = str(row[key]) if row[key] else None
        return row

    @staticmethod
    async def _version(name: str, version: int) -> dict | None:
        result = await db.execute_system(
            "SELECT index_name,version,index_id,model_id,model_revision,dimensions,metric,"
            "build_status,indexed_rows,failure_code,created_at,completed_at "
            "FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
            "WHERE index_name=%s AND version=%s",
            [name, version],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        row["created_at"] = str(row["created_at"])
        row["completed_at"] = str(row["completed_at"]) if row["completed_at"] else None
        return row

    async def list(self, user: dict) -> list[dict]:
        result = await db.execute_system(
            "SELECT name FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES "
            "WHERE status<>'DEPRECATED' ORDER BY name"
        )
        visible = []
        for row in result["rows"]:
            definition = await self._get(row[0])
            if definition and await self._source_access(definition, user):
                visible.append(definition)
        return visible

    async def describe(self, name: str, user: dict) -> dict:
        definition = await self._get(name)
        if not definition or not await self._source_access(definition, user):
            raise HTTPException(status_code=404, detail="Search index not found")
        versions = await db.execute_system(
            "SELECT version FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
            "WHERE index_name=%s ORDER BY version DESC",
            [name],
        )
        return {
            **definition,
            "versions": [await self._version(name, row[0]) for row in versions["rows"]],
        }

    async def create(self, body: SearchIndexCreate, user: dict) -> dict:
        definition = body.model_dump()
        if not await self._source_access(definition, user):
            raise HTTPException(status_code=403, detail="Search source is unavailable")
        if body.entity_id:
            entity = await entity_registry.get(body.entity_id, user)
            if (
                not entity
                or entity.relation != body.source_relation
                or entity.key_columns != body.key_columns
            ):
                raise HTTPException(
                    status_code=422, detail="Entity identity does not match search source"
                )
        capabilities = await vector_backend.health()
        if not capabilities["full_text"] or (body.model_alias and not capabilities["vector"]):
            raise HTTPException(
                status_code=503, detail="Search indexing is unavailable on this cluster"
            )
        model = (
            await embedding_service.resolve_model(alias=body.model_alias)
            if body.model_alias
            else None
        )
        if model and model.dimensions > 4096:
            raise HTTPException(
                status_code=422, detail="Embedding dimensions exceed vector backend limit"
            )
        async with self._lock(body.name):
            if await self._get(body.name):
                raise HTTPException(status_code=409, detail="Search index already exists")
            index_id = str(uuid4())
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES "
                "(name,id,source_relation,key_columns,content_columns,filter_columns,entity_id,"
                "model_alias,owner_name,active_version,status,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,'BUILDING',NOW(),NOW())",
                [
                    body.name,
                    index_id,
                    body.source_relation,
                    json.dumps(body.key_columns),
                    json.dumps(body.content_columns),
                    json.dumps(body.filter_columns),
                    body.entity_id,
                    body.model_alias,
                    user["username"],
                ],
            )
            await self._insert_version(body.name, index_id, 1, model)
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="CREATE",
            object_type="AI_SEARCH_INDEX",
            object_name=body.name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        self._schedule(body.name, 1)
        return await self.describe(body.name, user)

    @staticmethod
    async def _insert_version(
        name: str, index_id: str, version: int, model: ResolvedEmbeddingModel | None
    ) -> None:
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
            "(index_name,version,index_id,model_id,model_revision,dimensions,metric,"
            "build_status,indexed_rows,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'PENDING',0,NOW())",
            [
                name,
                version,
                index_id,
                model.model_id if model else None,
                model.revision if model else None,
                model.dimensions if model else 0,
                model.metric if model else "cosine",
            ],
        )
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE "
            "(index_name,version,source_rows,indexed_rows,status,updated_at) "
            "VALUES (%s,%s,0,0,'PENDING',NOW())",
            [name, version],
        )

    def _schedule(self, name: str, version: int) -> None:
        task = asyncio.create_task(self.build(name, version), name=f"search:{name}:{version}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(self) -> None:
        if self._poller is None:
            self._poller = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        if self._poller:
            self._poller.cancel()
            with suppress(asyncio.CancelledError):
                await self._poller
            self._poller = None
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _poll(self) -> None:
        ticks = 0
        while True:
            try:
                result = await db.execute_system(
                    "SELECT index_name,version FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                    "WHERE build_status IN ('PENDING','BUILDING','AUTO_PENDING',"
                    "'AUTO_BUILDING') LIMIT 100"
                )
                scheduled = {task.get_name() for task in self._tasks if not task.done()}
                for name, version in result["rows"]:
                    key = f"search:{name}:{version}"
                    if key not in scheduled:
                        self._schedule(name, version)
                ticks += 1
                if ticks % 4 == 0:
                    await self._reconcile()
            except Exception as exc:
                logger.warning("Search build poll failed: %s", type(exc).__name__)
            await asyncio.sleep(15)

    async def _source_fingerprint(self, definition: dict) -> str:
        columns = [
            *definition["key_columns"],
            *definition["content_columns"],
            *definition["filter_columns"],
        ]
        ordering = ",".join(quote_identifier(name) for name in definition["key_columns"])
        source = quote_source(definition["source_relation"])
        select = ",".join(quote_identifier(name) for name in columns)
        digest = hashlib.sha256()
        offset = 0
        while True:
            if offset >= _MAX_SOURCE_ROWS:
                raise ValueError("Search source exceeds reconciliation limit")
            result = await db.execute_system(
                f"SELECT {select} FROM {source} ORDER BY {ordering} LIMIT {offset},{_BATCH_SIZE}"
            )
            rows = result["rows"]
            for row in rows:
                digest.update(json.dumps(row, default=str, ensure_ascii=False).encode())
                digest.update(b"\n")
            offset += len(rows)
            if len(rows) < _BATCH_SIZE:
                break
        return digest.hexdigest()

    async def _reconcile(self) -> None:
        result = await db.execute_system(
            "SELECT name,active_version FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES "
            "WHERE status='ACTIVE' AND active_version IS NOT NULL LIMIT 100"
        )
        for name, active_version in result["rows"]:
            try:
                definition = await self._get(name)
                if not definition:
                    continue
                state = await db.execute_system(
                    "SELECT last_watermark FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE "
                    "WHERE index_name=%s AND version=%s",
                    [name, active_version],
                )
                if not state["rows"] or not state["rows"][0][0]:
                    continue
                current = await self._source_fingerprint(definition)
                if current == state["rows"][0][0]:
                    continue
                async with self._lock(name):
                    latest = await db.execute_system(
                        "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                        "WHERE index_name=%s",
                        [name],
                    )
                    number = int(latest["rows"][0][0] or 0)
                    if number != active_version:
                        continue
                    active = await self._version(name, active_version)
                    if active is None:
                        continue
                    model = (
                        await embedding_service.resolve_model(
                            model_id=active["model_id"], revision=active["model_revision"]
                        )
                        if active["model_id"]
                        else None
                    )
                    await self._insert_version(name, definition["id"], number + 1, model)
                    await db.execute_system(
                        "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                        "SET build_status='AUTO_PENDING' WHERE index_name=%s AND version=%s",
                        [name, number + 1],
                    )
                self._schedule(name, number + 1)
            except Exception as exc:
                logger.warning("Search reconciliation failed: %s", type(exc).__name__)

    async def build(self, name: str, version: int) -> None:
        try:
            async with self._lock(name):
                definition = await self._get(name)
                version_row = await self._version(name, version)
                if (
                    not definition
                    or not version_row
                    or version_row["build_status"]
                    not in {"PENDING", "BUILDING", "AUTO_PENDING", "AUTO_BUILDING"}
                ):
                    return
                await self._build_locked(definition, version_row)
        except HTTPException:
            return

    async def _build_locked(self, definition: dict, version: dict) -> None:
        name = definition["name"]
        number = version["version"]
        model = None
        projection = SearchProjection(
            definition["id"],
            number,
            version["dimensions"],
            version["metric"],
            lexical_only=version["model_id"] is None,
        )
        started = time.monotonic()
        automatic = version["build_status"].startswith("AUTO_")
        try:
            if version["model_id"]:
                model = await embedding_service.resolve_model(
                    model_id=version["model_id"], revision=version["model_revision"]
                )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                "SET build_status=%s,failure_code=NULL "
                "WHERE index_name=%s AND version=%s",
                ["AUTO_BUILDING" if automatic else "BUILDING", name, number],
            )
            await vector_backend.drop_index(projection)
            await vector_backend.create_index(projection)
            source = quote_source(definition["source_relation"])
            columns = (
                *definition["key_columns"],
                *definition["content_columns"],
                *definition["filter_columns"],
            )
            select = ",".join(quote_identifier(column) for column in columns)
            ordering = ",".join(quote_identifier(column) for column in definition["key_columns"])
            previous = (
                await self._version(name, definition["active_version"])
                if definition["active_version"]
                else None
            )
            previous_table = (
                SearchProjection(
                    definition["id"],
                    previous["version"],
                    previous["dimensions"],
                    previous["metric"],
                    previous["model_id"] is None,
                ).table()
                if previous
                and previous["model_id"] == version["model_id"]
                and previous["model_revision"] == version["model_revision"]
                and previous["dimensions"] == version["dimensions"]
                else None
            )
            count = 0
            seen = set()
            digest = hashlib.sha256()
            while True:
                if count > _MAX_SOURCE_ROWS or time.monotonic() - started > _BUILD_TIMEOUT_SECONDS:
                    raise ValueError("Search build budget exceeded")
                result = await db.execute_system(
                    f"SELECT {select} FROM {source} ORDER BY {ordering} LIMIT {count},{_BATCH_SIZE}"
                )
                rows = result["rows"]
                if not rows:
                    break
                for row in rows:
                    digest.update(json.dumps(row, default=str, ensure_ascii=False).encode())
                    digest.update(b"\n")
                key_count = len(definition["key_columns"])
                contents = len(definition["content_columns"])
                texts = [
                    " ".join(
                        str(value)
                        for value in row[key_count : key_count + contents]
                        if value is not None
                    )
                    for row in rows
                ]
                hashes = [
                    hashlib.sha256(
                        (content + (version["model_revision"] or "")).encode()
                    ).hexdigest()
                    for content in texts
                ]
                source_keys = [_source_key(row[:key_count]) for row in rows]
                reusable: set[str] = set()
                if previous_table and model:
                    marks = ",".join("%s" for _ in source_keys)
                    existing = await db.execute_system(
                        f"SELECT source_key,content_hash FROM {previous_table} "
                        f"WHERE source_key IN ({marks})",
                        source_keys,
                    )
                    old_hash = {item[0]: item[1] for item in existing["rows"]}
                    reusable = {
                        key
                        for key, content_hash in zip(source_keys, hashes, strict=True)
                        if old_hash.get(key) == content_hash
                    }
                missing = [
                    content
                    for key, content in zip(source_keys, texts, strict=True)
                    if key not in reusable
                ]
                embedded = iter(
                    await embedding_service.embed_batch(missing, model) if model and missing else []
                )
                for row, content, source_key, content_hash in zip(
                    rows, texts, source_keys, hashes, strict=True
                ):
                    if source_key in seen:
                        raise ValueError("Duplicate source identity")
                    seen.add(source_key)
                    metadata = dict(
                        zip(definition["filter_columns"], row[key_count + contents :], strict=True)
                    )
                    if source_key in reusable:
                        await db.execute_system(
                            f"INSERT INTO {projection.table()} "
                            "(source_key,content,metadata,embedding,content_hash,model_id,"
                            "model_revision,indexed_at) SELECT %s,%s,parse_json(%s),"
                            f"embedding,%s,%s,%s,NOW() FROM {previous_table} "
                            "WHERE source_key=%s AND content_hash=%s LIMIT 1",
                            [
                                source_key,
                                content,
                                json.dumps(metadata, default=str),
                                content_hash,
                                version["model_id"],
                                version["model_revision"],
                                source_key,
                                content_hash,
                            ],
                        )
                        continue
                    vector = next(embedded) if model else None
                    literal = (
                        "NULL" if vector is None else "[" + ",".join(str(x) for x in vector) + "]"
                    )
                    await db.execute_system(
                        f"INSERT INTO {projection.table()} "
                        "(source_key,content,metadata,embedding,content_hash,model_id,"
                        "model_revision,indexed_at) VALUES "
                        f"(%s,%s,parse_json(%s),{literal},%s,%s,%s,NOW())",
                        [
                            source_key,
                            content,
                            json.dumps(metadata, default=str),
                            content_hash,
                            version["model_id"],
                            version["model_revision"],
                        ],
                    )
                count += len(rows)
                await db.execute_system(
                    "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE "
                    "SET source_rows=%s,indexed_rows=%s,status='BUILDING',updated_at=NOW() "
                    "WHERE index_name=%s AND version=%s",
                    [count, count, name, number],
                )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                "SET build_status='READY',indexed_rows=%s,completed_at=NOW() "
                "WHERE index_name=%s AND version=%s",
                [count, name, number],
            )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE "
                "SET status='READY',source_rows=%s,indexed_rows=%s,"
                "last_watermark=%s,updated_at=NOW() "
                "WHERE index_name=%s AND version=%s",
                [count, count, digest.hexdigest(), name, number],
            )
            if automatic or definition["active_version"] is None:
                await self._activate_locked(name, number)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                "SET build_status='FAILED',failure_code=%s,completed_at=NOW() "
                "WHERE index_name=%s AND version=%s",
                [type(exc).__name__[:64], name, number],
            )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE "
                "SET status='FAILED',updated_at=NOW() "
                "WHERE index_name=%s AND version=%s",
                [name, number],
            )

    @staticmethod
    async def _activate_locked(name: str, version: int) -> None:
        row = await SearchService._version(name, version)
        if not row or row["build_status"] not in {"READY", "ACTIVE", "DEPRECATED"}:
            raise HTTPException(status_code=409, detail="Search version is not ready")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
            "SET build_status='DEPRECATED' WHERE index_name=%s AND build_status='ACTIVE'",
            [name],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
            "SET build_status='ACTIVE' WHERE index_name=%s AND version=%s",
            [name, version],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES "
            "SET active_version=%s,status='ACTIVE',updated_at=NOW() WHERE name=%s",
            [version, name],
        )

    async def activate(self, name: str, version: int, user: dict) -> dict:
        definition = await self._owned(name, user)
        async with self._lock(name, wait_seconds=5):
            await self._activate_locked(name, version)
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="ACTIVATE",
            object_type="AI_SEARCH_INDEX",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        return await self.describe(definition["name"], user)

    async def _owned(self, name: str, user: dict) -> dict:
        definition = await self._get(name)
        if not definition or not can_manage(definition["owner_name"], user):
            raise HTTPException(status_code=404, detail="Search index not found")
        return definition

    async def rebuild(self, name: str, body: SearchRebuild, user: dict) -> dict:
        definition = await self._owned(name, user)
        if not await self._source_access(definition, user):
            raise HTTPException(status_code=403, detail="Search source is unavailable")
        alias = body.model_alias or definition["model_alias"]
        model = await embedding_service.resolve_model(alias=alias) if alias else None
        capabilities = await vector_backend.health()
        if not capabilities["full_text"] or (model and not capabilities["vector"]):
            raise HTTPException(
                status_code=503, detail="Search indexing is unavailable on this cluster"
            )
        async with self._lock(name):
            result = await db.execute_system(
                "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                "WHERE index_name=%s",
                [name],
            )
            version = int(result["rows"][0][0] or 0) + 1
            await self._insert_version(name, definition["id"], version, model)
            if body.model_alias:
                await db.execute_system(
                    "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES "
                    "SET model_alias=%s,updated_at=NOW() WHERE name=%s",
                    [alias, name],
                )
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="REBUILD",
            object_type="AI_SEARCH_INDEX",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        self._schedule(name, version)
        return {"index": name, "version": version, "status": "PENDING"}

    async def retry(self, name: str, version: int, user: dict) -> dict:
        definition = await self._owned(name, user)
        if not await self._source_access(definition, user):
            raise HTTPException(status_code=403, detail="Search source is unavailable")
        async with self._lock(name):
            row = await self._version(name, version)
            if not row or row["build_status"] != "FAILED":
                raise HTTPException(status_code=409, detail="Only failed builds may be retried")
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS "
                "SET build_status='PENDING',failure_code=NULL WHERE index_name=%s AND version=%s",
                [name, version],
            )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE "
                "SET status='PENDING',updated_at=NOW() WHERE index_name=%s AND version=%s",
                [name, version],
            )
        self._schedule(name, version)
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="RETRY",
            object_type="AI_SEARCH_INDEX",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        return {"index": name, "version": version, "status": "PENDING"}

    async def drop(self, name: str, user: dict) -> None:
        definition = await self._owned(name, user)
        async with self._lock(name, wait_seconds=5):
            versions = await db.execute_system(
                "SELECT version,dimensions,metric,model_id FROM "
                "NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS WHERE index_name=%s",
                [name],
            )
            await db.execute_system(
                "UPDATE NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES "
                "SET status='DEPRECATED',active_version=NULL,updated_at=NOW() WHERE name=%s",
                [name],
            )
            for version, dimensions, metric, model_id in versions["rows"]:
                await vector_backend.drop_index(
                    SearchProjection(
                        definition["id"], version, dimensions, metric, model_id is None
                    )
                )
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_VERSIONS WHERE index_name=%s", [name]
            )
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_SYNC_STATE WHERE index_name=%s", [name]
            )
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_AI_SEARCH_INDEXES WHERE name=%s", [name]
            )
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="DROP",
            object_type="AI_SEARCH_INDEX",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )

    async def query(self, name: str, body: SearchQuery, user: dict) -> dict:
        started = time.monotonic()
        definition = await self._get(name)
        if (
            not definition
            or definition["status"] != "ACTIVE"
            or not await self._source_access(definition, user)
        ):
            raise HTTPException(status_code=404, detail="Search index not found")
        version = await self._version(name, definition["active_version"])
        if not version or version["build_status"] != "ACTIVE":
            raise HTTPException(status_code=503, detail="Active search version is unavailable")
        if body.mode != "LEXICAL" and not version["model_id"]:
            raise HTTPException(status_code=422, detail="This index supports lexical search only")
        projection = SearchProjection(
            definition["id"],
            version["version"],
            version["dimensions"],
            version["metric"],
            lexical_only=version["model_id"] is None,
        )
        filter_sql, filter_params = _sql_filters(body.filters, definition["filter_columns"])
        limit = min(body.top_k * 4, 400)
        lexical: list[SearchCandidate] = []
        semantic: list[SearchCandidate] = []
        if body.mode in {"LEXICAL", "HYBRID"}:
            clause = "content MATCH_ANY %s"
            if filter_sql:
                clause += " AND " + filter_sql
            result = await db.execute_system(
                f"SELECT source_key,content,metadata FROM {projection.table()} "
                f"WHERE {clause} ORDER BY source_key LIMIT {limit}",
                [body.query, *filter_params],
            )
            lexical = [
                SearchCandidate(row[0], row[1], _json(row[2]) or {}) for row in result["rows"]
            ]
        if body.mode in {"SEMANTIC", "HYBRID"}:
            model = await embedding_service.resolve_model(
                model_id=version["model_id"], revision=version["model_revision"]
            )
            vector = await embedding_service.embed(body.query, model)
            sql = vector_backend.vector_sql(projection, vector, limit)
            if filter_sql:
                sql = sql.replace(" ORDER BY ", f" WHERE {filter_sql} ORDER BY ", 1)
            result = await db.execute_system(sql, filter_params)
            semantic = [
                SearchCandidate(row[0], row[1], _json(row[2]) or {}, float(row[3]))
                for row in result["rows"]
            ]
        ranked = fuse_results(lexical, semantic, mode=body.mode, top_k=100, rrf_k=body.rrf_k)
        hits: list[dict[str, Any]] = []
        for hit in ranked:
            key = json.loads(hit.source_key)
            if not isinstance(key, list) or len(key) != len(definition["key_columns"]):
                continue
            predicate = " AND ".join(
                f"{quote_identifier(column)}=FROM_BASE64('{base64.b64encode(str(value).encode()).decode()}')"
                for column, value in zip(definition["key_columns"], key, strict=True)
            )
            columns = [*definition["content_columns"], *definition["filter_columns"]]
            source = await query_service.execute(
                sql=(
                    "SELECT "
                    + ",".join(quote_identifier(column) for column in columns)
                    + f" FROM {quote_source(definition['source_relation'])} "
                    + f"WHERE {predicate} LIMIT 1"
                ),
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                database=definition["source_relation"].split(".")[-2],
                role=user.get("active_role"),
                session_id=user.get("session_id"),
                max_rows=1,
            )
            if source.error or not source.rows:
                continue
            current = source.rows[0]
            content_count = len(definition["content_columns"])
            metadata = dict(zip(definition["filter_columns"], current[content_count:], strict=True))
            if any(
                str(metadata.get(column)) != str(value) for column, value in body.filters.items()
            ):
                continue
            current_hit = hit.__dict__.copy()
            current_hit["content"] = " ".join(
                str(value) for value in current[:content_count] if value is not None
            )
            current_hit["metadata"] = metadata
            current_hit["rank"] = len(hits) + 1
            hits.append(current_hit)
            if len(hits) >= body.top_k:
                break
        elapsed_ms = int((time.monotonic() - started) * 1000)
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.AI_SEARCH_QUERY_LOG "
            "(id,index_name,index_version,user_name,mode,elapsed_ms,lexical_candidates,"
            "semantic_candidates,result_count,status,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'SUCCESS',NOW())",
            [
                str(uuid4()),
                name,
                version["version"],
                user["username"],
                body.mode,
                elapsed_ms,
                len(lexical),
                len(semantic),
                len(hits),
            ],
        )
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="QUERY",
            object_type="AI_SEARCH_INDEX",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        return {
            "index": name,
            "version": version["version"],
            "mode": body.mode,
            "elapsed_ms": elapsed_ms,
            "lexical_candidates": len(lexical),
            "semantic_candidates": len(semantic),
            "hits": hits,
        }

    async def evaluate(self, name: str, body: SearchEvalRequest, user: dict) -> dict:
        reports = []
        version = None
        for case in body.cases:
            result = await self.query(
                name, SearchQuery(query=case.query, mode=body.mode, top_k=body.top_k), user
            )
            version = result["version"]
            scored = evaluate_relevance(
                [hit["source_key"] for hit in result["hits"]],
                case.relevant,
                top_k=body.top_k,
            )
            reports.append({"query": case.query, **scored.__dict__})
        names = ("precision_at_k", "recall_at_k", "mrr", "ndcg_at_k")
        summary = {metric: sum(row[metric] for row in reports) / len(reports) for metric in names}
        summary["zero_result_rate"] = sum(row["zero_result"] for row in reports) / len(reports)
        eval_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.AI_SEARCH_EVAL_RUNS "
            "(id,index_name,index_version,user_name,mode,query_count,metrics,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())",
            [
                eval_id,
                name,
                version,
                user["username"],
                body.mode,
                len(reports),
                json.dumps(summary),
            ],
        )
        await write_audit_log(
            event_type="AI_SEARCH",
            user_name=user["username"],
            action="EVALUATE",
            object_type="AI_SEARCH_INDEX",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )
        return {
            "id": eval_id,
            "index": name,
            "version": version,
            "mode": body.mode,
            "summary": summary,
            "cases": reports,
        }


search_service = SearchService()


@router.get("", response_class=SanitizingJSONResponse)
async def list_indexes(user: CurrentUser):
    return await search_service.list(user)


@router.post("", status_code=201, response_class=SanitizingJSONResponse)
async def create_index(body: SearchIndexCreate, user: AdminUser):
    return await search_service.create(body, user)


@router.get("/{name}", response_class=SanitizingJSONResponse)
async def describe_index(name: str, user: CurrentUser):
    return await search_service.describe(name, user)


@router.post("/{name}/query", response_class=SanitizingJSONResponse)
async def query_index(name: str, body: SearchQuery, user: CurrentUser):
    return await search_service.query(name, body, user)


@router.post("/{name}/evaluate", response_class=SanitizingJSONResponse)
async def evaluate_index(name: str, body: SearchEvalRequest, user: CurrentUser):
    return await search_service.evaluate(name, body, user)


@router.post("/{name}/rebuild", status_code=202, response_class=SanitizingJSONResponse)
async def rebuild_index(name: str, body: SearchRebuild, user: AdminUser):
    return await search_service.rebuild(name, body, user)


@router.post(
    "/{name}/versions/{version}/retry", status_code=202, response_class=SanitizingJSONResponse
)
async def retry_index_build(name: str, version: int, user: AdminUser):
    return await search_service.retry(name, version, user)


@router.post("/{name}/versions/{version}/activate", response_class=SanitizingJSONResponse)
async def activate_index(name: str, version: int, user: AdminUser):
    return await search_service.activate(name, version, user)


@router.delete("/{name}", status_code=204)
async def drop_index(name: str, user: AdminUser):
    await search_service.drop(name, user)
