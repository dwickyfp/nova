"""Versioned AI Search indexes with caller-scoped retrieval and managed projections."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.database import db
from app.core.deps import get_current_user, require_role
from app.core.metadata_db import (
    MetadataConflict,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.modules.access_control.security_context import SecurityContext
from app.modules.agents.semantic.expressions import quote_identifier, quote_source
from app.modules.ai_ml.embeddings import ResolvedEmbeddingModel, embedding_service
from app.modules.intelligence.access import can_manage
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.metadata_repository import (
    intelligence_metadata,
    service_actor,
    user_actor,
)
from app.modules.intelligence.retrieval import SearchCandidate, evaluate_relevance, fuse_results
from app.modules.intelligence.vector_backend import SearchProjection, vector_backend
from app.modules.query.service import query_service
from app.modules.users.router import ADMIN_ROLES
from app.observability.metrics import (
    SEARCH_BUILD_DURATION,
    SEARCH_BUILD_ERRORS,
    SEARCH_BUILDS,
    SEARCH_BUILDS_ACTIVE,
    SEARCH_LAST_SUCCESSFUL_POLL,
    SEARCH_POLL_ERRORS,
    SEARCH_RECONCILIATION_ERRORS,
)

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser = Annotated[dict, Depends(require_role(*ADMIN_ROLES))]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BATCH_SIZE = 32
_MAX_SOURCE_ROWS = 1_000_000
_BUILD_TIMEOUT_SECONDS = 3600
#: A build that records no progress for this long has lapsed; the poller takes it over.
_BUILD_LEASE_SECONDS = 300
_BUILDER = "ai-search-builder"
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


_INDEX_FIELDS = (
    "name",
    "id",
    "source_relation",
    "key_columns",
    "content_columns",
    "filter_columns",
    "entity_id",
    "model_alias",
    "owner_name",
    "active_version",
    "status",
    "created_at",
    "updated_at",
)
_VERSION_FIELDS = (
    "index_name",
    "version",
    "index_id",
    "model_id",
    "model_revision",
    "dimensions",
    "metric",
    "build_status",
    "indexed_rows",
    "failure_code",
    "created_at",
    "completed_at",
)
_CLAIMABLE = frozenset({"PENDING", "BUILDING", "AUTO_PENDING", "AUTO_BUILDING"})
_SERVABLE = frozenset({"READY", "ACTIVE", "DEPRECATED"})
_EMBEDDING = ("model_id", "model_revision", "dimensions", "metric")


class _BuildStopped(Exception):
    """The build lost its claim or its index was dropped; it must not record a result."""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _timestamp(value: Any) -> str | None:
    return str(value) if value else None


def _definition(row: dict) -> dict[str, Any]:
    """The API shape of an index row."""
    definition = {key: row[key] for key in _INDEX_FIELDS}
    for key in ("key_columns", "content_columns", "filter_columns"):
        definition[key] = _json(definition[key])
    for key in ("created_at", "updated_at"):
        definition[key] = _timestamp(definition[key])
    return definition


def _version_record(row: dict) -> dict[str, Any]:
    """The API shape of a version row."""
    record = {key: row[key] for key in _VERSION_FIELDS}
    record["created_at"] = str(record["created_at"])
    record["completed_at"] = _timestamp(record["completed_at"])
    return record


def _model_fields(model: ResolvedEmbeddingModel | None) -> dict[str, Any]:
    if model is None:
        return {"model_id": None, "model_revision": None, "dimensions": 0, "metric": "cosine"}
    return {
        "model_id": model.model_id,
        "model_revision": model.revision,
        "dimensions": model.dimensions,
        "metric": model.metric,
    }


def _promotion_target(index: dict, versions: list[dict]) -> int | None:
    """The built version an index serves without an explicit activation.

    A first build is served once it is ready, and a newer build with the served
    embedding model (a source refresh) replaces the served one. A build with
    another model waits for an explicit activation. Only the API may activate,
    so the build worker leaves this step to the next API read.
    """
    if index["status"] == "DEPRECATED":
        return None
    active = index["active_version"]
    ready = [
        row for row in versions if row["build_status"] == "READY" and row["version"] > (active or 0)
    ]
    if not ready:
        return None
    newest = max(ready, key=lambda row: row["version"])
    if active is None:
        return int(newest["version"])
    served = next((row for row in versions if row["version"] == active), None)
    if served and all(served[key] == newest[key] for key in _EMBEDDING):
        return int(newest["version"])
    return None


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

    @staticmethod
    def _builder() -> SecurityContext:
        """The maintenance worker's own service principal for builds and refreshes."""
        return service_actor(_BUILDER)

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
    async def _snapshot(name: str, actor: SecurityContext) -> tuple[dict | None, list[dict]]:
        """An index row and its versions (newest first) from one read."""
        async with intelligence_metadata(actor, read_only=True) as repo:
            row = await repo.get_index(name)
            versions = await repo.list_search_versions(name) if row else []
        return row, versions

    @staticmethod
    async def _promote(name: str, actor: SecurityContext) -> bool:
        """Serve a newly built version; False when nothing changed."""
        try:
            async with intelligence_metadata(actor) as repo:
                index = await repo.get_index(name)
                if index is None:
                    return False
                target = _promotion_target(index, await repo.list_search_versions(name))
                if target is None:
                    return False
                await repo.activate(name, target, expected_revision=index["metadata_revision"])
                return True
        except (MetadataConflict, MetadataPermissionDenied, MetadataReadOnlyMaintenance):
            # A concurrent activation won, this workload may not activate (only
            # the API may) or writes are paused: the caller reads current state.
            return False

    async def _visible(self, name: str, user: dict, actor: SecurityContext) -> tuple[dict, list]:
        """A live index the caller can read, with newly built versions served first."""
        row, versions = await self._snapshot(name, actor)
        if (
            not row
            or row["status"] == "DEPRECATED"
            or not await self._source_access(_definition(row), user)
        ):
            raise HTTPException(status_code=404, detail="Search index not found")
        if _promotion_target(row, versions) is not None and await self._promote(name, actor):
            row, versions = await self._snapshot(name, actor)
            if not row or row["status"] == "DEPRECATED":
                raise HTTPException(status_code=404, detail="Search index not found")
        return row, versions

    async def list(self, user: dict) -> list[dict]:
        actor = user_actor(user)
        async with intelligence_metadata(actor, read_only=True) as repo:
            rows = await repo.list_indexes()
            versions = {row["name"]: await repo.list_search_versions(row["name"]) for row in rows}
        visible = []
        for row in rows:
            definition = _definition(row)
            if not await self._source_access(definition, user):
                continue
            if _promotion_target(row, versions[row["name"]]) is not None and await self._promote(
                row["name"], actor
            ):
                refreshed, _ = await self._snapshot(row["name"], actor)
                if not refreshed or refreshed["status"] == "DEPRECATED":
                    continue
                definition = _definition(refreshed)
            visible.append(definition)
        return visible

    async def describe(self, name: str, user: dict) -> dict:
        row, versions = await self._visible(name, user, user_actor(user))
        return {**_definition(row), "versions": [_version_record(item) for item in versions]}

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
        try:
            # The index and its PENDING first version are the build's persisted
            # intent; the maintenance worker claims it from there.
            async with intelligence_metadata(user_actor(user)) as repo:
                if await repo.get_index(body.name):
                    raise HTTPException(status_code=409, detail="Search index already exists")
                await repo.create_index({**definition, "id": str(uuid4())}, _model_fields(model))
        except MetadataConflict:
            raise HTTPException(status_code=409, detail="Search index already exists") from None
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
        return await self.describe(body.name, user)

    def _schedule(self, name: str, version: int) -> None:
        task = asyncio.create_task(self.build(name, version), name=f"search:{name}:{version}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(self) -> None:
        if self._poller is None:
            self._poller = asyncio.create_task(self._poll())

    async def run(self) -> None:
        """Own the poll/build lifecycle in the maintenance worker."""
        if self._poller is not None:
            raise RuntimeError("Search polling is already running")
        await self.start()
        try:
            assert self._poller is not None
            await self._poller
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._poller:
            self._poller.cancel()
            await asyncio.gather(self._poller, return_exceptions=True)
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
        self._poller = None

    async def _poll(self) -> None:
        ticks = 0
        while True:
            try:
                # Pending versions, and builds whose builder stopped recording
                # progress (a crashed or stalled claim the poller takes over).
                async with intelligence_metadata(self._builder(), read_only=True) as repo:
                    pending = await repo.pending_builds(lease_seconds=_BUILD_LEASE_SECONDS)
                scheduled = {task.get_name() for task in self._tasks if not task.done()}
                for row in pending:
                    name, version = row["index_name"], row["version"]
                    if f"search:{name}:{version}" not in scheduled:
                        self._schedule(name, version)
                SEARCH_LAST_SUCCESSFUL_POLL.set(time.time())
                ticks += 1
                if ticks % 4 == 0:
                    try:
                        await self._reconcile()
                    except Exception as exc:
                        logger.warning("Search reconciliation failed: %s", type(exc).__name__)
            except Exception as exc:
                SEARCH_POLL_ERRORS.inc()
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
        actor = self._builder()
        try:
            async with intelligence_metadata(actor, read_only=True) as repo:
                indexes = await repo.list_indexes(active_only=True, limit=100)
        except Exception:
            SEARCH_RECONCILIATION_ERRORS.inc()
            raise
        for index in indexes:
            try:
                name, active_version = index["name"], index["active_version"]
                definition = _definition(index)
                async with intelligence_metadata(actor, read_only=True) as repo:
                    state = await repo.get_sync_state(name, active_version)
                if not state or not state["last_watermark"]:
                    continue
                current = await self._source_fingerprint(definition)
                if current == state["last_watermark"]:
                    continue
                async with intelligence_metadata(actor, read_only=True) as repo:
                    latest = await repo.latest_search_version(name)
                    active = await repo.get_search_version(name, active_version)
                if latest != active_version or active is None:
                    continue
                model = (
                    await embedding_service.resolve_model(
                        model_id=active["model_id"], revision=active["model_revision"]
                    )
                    if active["model_id"]
                    else None
                )
                try:
                    async with intelligence_metadata(actor) as repo:
                        created = await repo.add_search_version(
                            name,
                            definition["id"],
                            _model_fields(model),
                            expected_latest=latest,
                            automatic=True,
                        )
                except MetadataConflict:
                    continue  # another writer created the next version first
                self._schedule(name, created["version"])
            except Exception as exc:
                SEARCH_RECONCILIATION_ERRORS.inc()
                logger.warning("Search reconciliation failed: %s", type(exc).__name__)

    async def build(self, name: str, version: int) -> None:
        started = time.monotonic()
        status = "skipped"
        try:
            claim = await self._claim(name, version)
            if claim is None:
                return
            definition, version_row, previous, generation = claim
            SEARCH_BUILDS_ACTIVE.inc()
            try:
                built = await self._build_claimed(definition, version_row, generation, previous)
                status = "superseded" if built is None else "success" if built else "failed"
            finally:
                SEARCH_BUILDS_ACTIVE.dec()
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception:
            status = "failed"
            raise
        finally:
            if status == "failed":
                SEARCH_BUILD_ERRORS.inc()
            SEARCH_BUILDS.labels(status=status).inc()
            SEARCH_BUILD_DURATION.labels(status=status).observe(time.monotonic() - started)

    async def _claim(self, name: str, version: int) -> tuple[dict, dict, dict | None, int] | None:
        """Claim a pending (or lapsed) build under a new generation."""
        async with intelligence_metadata(self._builder()) as repo:
            index = await repo.get_index(name)
            row = await repo.get_search_version(name, version)
            if (
                not index
                or index["status"] == "DEPRECATED"
                or not row
                or row["build_status"] not in _CLAIMABLE
            ):
                return None
            generation = await repo.claim_build(name, version)
            if generation is None:
                return None
            # The first heartbeat starts the lease that the poller measures.
            await repo.record_build_progress(
                name, version, generation=generation, source_rows=0, indexed_rows=0
            )
            previous = (
                await repo.get_search_version(name, index["active_version"])
                if index["active_version"]
                else None
            )
        return (
            _definition(index),
            _version_record(row),
            _version_record(previous) if previous else None,
            generation,
        )

    async def _heartbeat(
        self, name: str, version: int, generation: int, rows: int, projection: SearchProjection
    ) -> None:
        """Record progress under the claim, or stop a build that may no longer finish."""
        async with intelligence_metadata(self._builder()) as repo:
            index = await repo.get_index(name)
            dropped = not index or index["status"] == "DEPRECATED"
            recorded = not dropped and await repo.record_build_progress(
                name, version, generation=generation, source_rows=rows, indexed_rows=rows
            )
        if dropped:
            # The drop may have run before this build created its projection.
            await vector_backend.drop_index(projection)
            raise _BuildStopped
        if not recorded:
            # A newer claim owns this version and rebuilds the projection itself.
            raise _BuildStopped

    async def _finish(self, name: str, version: int, generation: int, **result: Any) -> bool:
        async with intelligence_metadata(self._builder()) as repo:
            return await repo.finish_build(name, version, generation=generation, **result)

    async def _build_claimed(
        self, definition: dict, version: dict, generation: int, previous: dict | None
    ) -> bool | None:
        """Build the engine projection; None when the claim was lost or the index dropped."""
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
        try:
            if version["model_id"]:
                model = await embedding_service.resolve_model(
                    model_id=version["model_id"], revision=version["model_revision"]
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
                await self._heartbeat(name, number, generation, count, projection)
            finished = await self._finish(
                name, number, generation, indexed_rows=count, watermark=digest.hexdigest()
            )
            # A lost claim leaves the result to the build that took it over.
            return True if finished else None
        except asyncio.CancelledError:
            raise
        except _BuildStopped:
            return None
        except Exception as exc:
            try:
                await self._finish(name, number, generation, failure_code=type(exc).__name__[:64])
            except Exception as failure:
                # The claim lapses and the poller takes the build over.
                logger.warning("Search build failure was not recorded: %s", type(failure).__name__)
            return False

    async def activate(self, name: str, version: int, user: dict) -> dict:
        definition = await self._owned(name, user)
        async with intelligence_metadata(user_actor(user)) as repo:
            index = await repo.get_index(name)
            row = await repo.get_search_version(name, version)
            if not index or index["status"] == "DEPRECATED":
                raise HTTPException(status_code=404, detail="Search index not found")
            if not row or row["build_status"] not in _SERVABLE:
                raise HTTPException(status_code=409, detail="Search version is not ready")
            await repo.activate(name, version, expected_revision=index["metadata_revision"])
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

    async def _owned(self, name: str, user: dict, *, include_dropped: bool = False) -> dict:
        async with intelligence_metadata(user_actor(user), read_only=True) as repo:
            row = await repo.get_index(name)
        if (
            not row
            or (row["status"] == "DEPRECATED" and not include_dropped)
            or not can_manage(row["owner_name"], user)
        ):
            raise HTTPException(status_code=404, detail="Search index not found")
        return _definition(row)

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
        async with intelligence_metadata(user_actor(user)) as repo:
            index = await repo.get_index(name)
            if not index or index["status"] == "DEPRECATED":
                raise HTTPException(status_code=404, detail="Search index not found")
            latest = await repo.latest_search_version(name)
            created = await repo.add_search_version(
                name, index["id"], _model_fields(model), expected_latest=latest
            )
            if body.model_alias:
                await repo.set_model_alias(
                    name, alias, expected_revision=index["metadata_revision"]
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
        return {"index": name, "version": created["version"], "status": "PENDING"}

    async def retry(self, name: str, version: int, user: dict) -> dict:
        """Queue a failed build again as the next version with the same embedding model.

        A failed version is terminal, so the retry is a new pending version.
        """
        definition = await self._owned(name, user)
        if not await self._source_access(definition, user):
            raise HTTPException(status_code=403, detail="Search source is unavailable")
        async with intelligence_metadata(user_actor(user)) as repo:
            index = await repo.get_index(name)
            row = await repo.get_search_version(name, version)
            if not index or index["status"] == "DEPRECATED":
                raise HTTPException(status_code=404, detail="Search index not found")
            if not row or row["build_status"] != "FAILED":
                raise HTTPException(status_code=409, detail="Only failed builds may be retried")
            latest = await repo.latest_search_version(name)
            created = await repo.add_search_version(
                name, index["id"], {key: row[key] for key in _EMBEDDING}, expected_latest=latest
            )
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
        return {"index": name, "version": created["version"], "status": "PENDING"}

    async def drop(self, name: str, user: dict) -> None:
        """Retire the index, then remove its engine projections.

        The retired rows are the persisted intent: a repeated drop of a retired
        index removes any projection that an earlier attempt left behind.
        """
        await self._owned(name, user, include_dropped=True)
        async with intelligence_metadata(user_actor(user)) as repo:
            index = await repo.get_index(name)
            if not index:
                raise HTTPException(status_code=404, detail="Search index not found")
            versions = await repo.list_search_versions(name)
            if index["status"] != "DEPRECATED":
                await repo.retire_index(name, expected_revision=index["metadata_revision"])
        for row in versions:
            await vector_backend.drop_index(
                SearchProjection(
                    index["id"],
                    row["version"],
                    row["dimensions"],
                    row["metric"],
                    row["model_id"] is None,
                )
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
        actor = user_actor(user)
        row, versions = await self._visible(name, user, actor)
        if row["status"] != "ACTIVE":
            raise HTTPException(status_code=404, detail="Search index not found")
        definition = _definition(row)
        version = next(
            (item for item in versions if item["version"] == row["active_version"]), None
        )
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
        async with intelligence_metadata(actor) as repo:
            await repo.log_query(
                {
                    "id": str(uuid4()),
                    "index_name": name,
                    "index_version": version["version"],
                    "mode": body.mode,
                    "elapsed_ms": elapsed_ms,
                    "lexical_candidates": len(lexical),
                    "semantic_candidates": len(semantic),
                    "result_count": len(hits),
                }
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
        async with intelligence_metadata(user_actor(user)) as repo:
            await repo.record_evaluation(
                {
                    "id": eval_id,
                    "index_name": name,
                    "index_version": version,
                    "mode": body.mode,
                    "query_count": len(reports),
                    "metrics": summary,
                }
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
