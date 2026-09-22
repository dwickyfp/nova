"""Credential-free StarRocks model/run metadata repository."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any
from uuid import uuid4

import asyncmy
import asyncmy.cursors
import redis.asyncio as aioredis

from app.core.config import settings
from app.modules.ml_engine.spec import MLExecutionSpec, VersionReservationConflict
from app.modules.query.sql_pipeline import redact_for_output


class ModelRegistryRepository:
    _local_locks: dict[str, asyncio.Lock] = {}

    async def _connect(self) -> asyncmy.Connection:
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
            connect_timeout=10,
        )

    async def reserve_version(
        self, spec: MLExecutionSpec, *, feature_columns: list[str]
    ) -> tuple[str, int]:
        """Reserve one unique TRAINING version without changing current_version."""
        assert spec.model_name
        scope = self._model_scope(spec)
        async with self._reservation_lock(scope):
            conn = await self._connect()
            try:
                async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                    await cursor.execute(
                        "SELECT model_id, current_version FROM NOVA_SYSTEM.ML_MODELS "
                        "WHERE model_name=%s AND created_by=%s AND "
                        "COALESCE(database_name, '')=COALESCE(%s, '') "
                        "AND COALESCE(schema_name, '')=COALESCE(%s, '') "
                        "ORDER BY updated_at DESC LIMIT 1",
                        (
                            spec.model_name,
                            spec.security.username,
                            spec.security.database,
                            spec.security.schema,
                        ),
                    )
                    row = await cursor.fetchone()
                    model_id = row["model_id"] if row else str(uuid4())
                    await cursor.execute(
                        "SELECT COALESCE(MAX(version), 0) AS version "
                        "FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id=%s",
                        (model_id,),
                    )
                    maximum = await cursor.fetchone()
                    registered = int(maximum["version"] or 0)
                    advertised = int(row.get("current_version") or 0) if row else 0
                    version = max(registered, advertised) + 1
                    current_version = advertised if row else 0
                    await cursor.execute(
                        "INSERT INTO NOVA_SYSTEM.ML_MODELS "
                        "(model_id, model_type, model_name, target_column, feature_columns, "
                        "hyperparameters, training_sql, database_name, schema_name, created_at, "
                        "created_by, current_version, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,NOW())",
                        (
                            model_id,
                            spec.task.value,
                            spec.model_name,
                            spec.target_column,
                            json.dumps(feature_columns),
                            json.dumps(spec.parameters, default=str),
                            spec.input_sql,
                            spec.security.database,
                            spec.security.schema,
                            spec.security.username,
                            current_version,
                        ),
                    )
                    await cursor.execute(
                        "INSERT INTO NOVA_SYSTEM.ML_MODEL_VERSIONS "
                        "(model_id,version,status,reservation_token,created_at,created_by) "
                        "VALUES (%s,%s,'TRAINING',%s,NOW(),%s)",
                        (model_id, version, str(uuid4()), spec.security.username),
                    )
                    return model_id, version
            finally:
                conn.close()

    async def register_version(
        self,
        *,
        model_id: str,
        version: int,
        spec: MLExecutionSpec,
        output,
        artifact_uri: str,
        artifact_sha256: str,
        artifact_size: int,
        training_duration_ms: int,
    ) -> None:
        async with self._reservation_lock(self._model_scope(spec)):
            conn = await self._connect()
            try:
                async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                    await cursor.execute(
                        "INSERT INTO NOVA_SYSTEM.ML_MODEL_VERSIONS "
                        "(model_id, version, status, training_rows, metrics, artifact_uri, "
                        "artifact_sha256, artifact_size, task, framework, framework_version, "
                        "algorithm, feature_schema, training_duration_ms, ready_at, "
                        "model_binary, created_at, created_by) "
                        "VALUES (%s,%s,'READY',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                        "NOW(),NULL,NOW(),%s)",
                        (
                            model_id,
                            version,
                            output.training_rows,
                            json.dumps(output.metrics, default=str),
                            artifact_uri,
                            artifact_sha256,
                            artifact_size,
                            spec.task.value,
                            output.engine,
                            _framework_version(output.engine),
                            output.algorithm,
                            json.dumps(output.metrics.get("feature_metadata", []), default=str),
                            training_duration_ms,
                            spec.security.username,
                        ),
                    )
                    await cursor.execute(
                        "SELECT current_version FROM NOVA_SYSTEM.ML_MODELS WHERE model_id=%s",
                        (model_id,),
                    )
                    row = await cursor.fetchone()
                    current = int(row.get("current_version") or 0) if row else 0
                    if version > current:
                        await cursor.execute(
                            "UPDATE NOVA_SYSTEM.ML_MODELS SET current_version=%s,"
                            "updated_at=NOW() WHERE model_id=%s",
                            (version, model_id),
                        )
            finally:
                conn.close()

    async def abort_version(self, model_id: str, version: int) -> bool:
        """Mark a partial version failed without moving the production pointer."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT current_version FROM NOVA_SYSTEM.ML_MODELS WHERE model_id=%s",
                    (model_id,),
                )
                model = await cursor.fetchone()
                if model and int(model.get("current_version") or 0) == version:
                    return False
                await cursor.execute(
                    "UPDATE NOVA_SYSTEM.ML_MODEL_VERSIONS SET status='FAILED',failed_at=NOW(),"
                    "failure_reason='artifact registration did not complete' "
                    "WHERE model_id=%s AND version=%s",
                    (model_id, version),
                )
                return True
        finally:
            conn.close()

    @asynccontextmanager
    async def _reservation_lock(self, scope: str):
        """Serialize allocation across replicas with Redis plus a local lock."""
        digest = hashlib.sha256(scope.encode()).hexdigest()
        local = self._local_locks.setdefault(digest, asyncio.Lock())
        async with local:
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            key = f"nova:ml:version:{digest}"
            token = str(uuid4())
            deadline = time.monotonic() + 10
            acquired = False
            try:
                while time.monotonic() < deadline:
                    if await client.set(key, token, nx=True, ex=30):
                        acquired = True
                        break
                    await asyncio.sleep(0.05)
                if not acquired:
                    raise VersionReservationConflict(
                        "Could not reserve a model version within 10 seconds; "
                        "retry the training job"
                    )
                yield
            finally:
                if acquired:
                    await client.eval(
                        "if redis.call('get', KEYS[1]) == ARGV[1] then return "
                        "redis.call('del', KEYS[1]) else return 0 end",
                        1,
                        key,
                        token,
                    )
                await client.aclose()

    @staticmethod
    def _model_scope(spec: MLExecutionSpec) -> str:
        return (
            f"{spec.security.tenant}:{spec.security.username}:"
            f"{spec.security.database or ''}:{spec.security.schema or ''}:"
            f"{spec.model_name or ''}"
        )

    async def record_run(
        self,
        *,
        run_id: str,
        spec: MLExecutionSpec,
        status: str,
        fingerprint: str,
        telemetry: dict[str, Any],
        model_id: str | None = None,
        version: int | None = None,
        artifact_uri: str | None = None,
        error: Exception | None = None,
    ) -> None:
        conn = await self._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO NOVA_SYSTEM.ML_RUNS "
                    "(run_id, task, mode, status, owner_name, tenant_name, database_name, "
                    "model_id, model_version, artifact_uri, fingerprint, telemetry, error_class, "
                    "error_message, created_at, expires_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),"
                    "DATE_ADD(NOW(), INTERVAL %s SECOND),NOW())",
                    (
                        run_id,
                        spec.task.value,
                        spec.mode.value,
                        status,
                        spec.security.username,
                        spec.security.tenant,
                        spec.security.database,
                        model_id,
                        version,
                        artifact_uri,
                        fingerprint,
                        json.dumps(telemetry, default=str),
                        type(error).__name__ if error else None,
                        redact_for_output(str(error))[:2000] if error else None,
                        settings.ML_EPHEMERAL_TTL_SECONDS,
                    ),
                )
        finally:
            conn.close()

    async def resolve_alias(
        self, alias: str, *, owner_name: str, database_name: str | None
    ) -> dict[str, Any] | None:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT a.model_id, a.version, m.model_name, m.model_type, "
                    "v.artifact_uri, v.artifact_sha256, v.artifact_size, v.model_binary "
                    "FROM NOVA_SYSTEM.ML_MODEL_ALIASES a "
                    "JOIN NOVA_SYSTEM.ML_MODELS m ON m.model_id=a.model_id "
                    "JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v "
                    "ON v.model_id=a.model_id AND v.version=a.version "
                    "WHERE a.alias_name=%s AND a.owner_name=%s AND a.database_name=%s "
                    "AND v.status='READY'",
                    (alias, owner_name, database_name or ""),
                )
                row = await cursor.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    async def get_version(
        self,
        model_id: str,
        version: int,
        *,
        owner_name: str,
        database_name: str | None,
    ) -> dict[str, Any] | None:
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT v.*, m.model_name, m.model_type FROM NOVA_SYSTEM.ML_MODEL_VERSIONS v "
                    "JOIN NOVA_SYSTEM.ML_MODELS m ON m.model_id=v.model_id "
                    "WHERE v.model_id=%s AND v.version=%s AND m.created_by=%s "
                    "AND COALESCE(m.database_name, '')=COALESCE(%s, '') "
                    "AND v.status='READY'",
                    (model_id, version, owner_name, database_name),
                )
                row = await cursor.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    async def set_alias(
        self,
        alias: str,
        model_id: str,
        version: int,
        *,
        owner_name: str,
        database_name: str | None,
    ) -> dict[str, Any]:
        if (
            await self.get_version(
                model_id,
                version,
                owner_name=owner_name,
                database_name=database_name,
            )
            is None
        ):
            raise ValueError("Model version not found")
        conn = await self._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO NOVA_SYSTEM.ML_MODEL_ALIASES "
                    "(alias_name, owner_name, database_name, model_id, version, "
                    "created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,NOW(),NOW())",
                    (alias, owner_name, database_name or "", model_id, version),
                )
        finally:
            conn.close()
        return {
            "alias_name": alias,
            "model_id": model_id,
            "version": version,
            "owner_name": owner_name,
            "database_name": database_name or "",
        }


model_registry_repository = ModelRegistryRepository()


def _framework_version(engine: str) -> str:
    distributions = {
        "flaml": "FLAML",
        "statsforecast": "statsforecast",
        "pyod": "pyod",
        "sklearn": "scikit-learn",
        "scikit_learn_clustering": "scikit-learn",
    }
    try:
        return package_version(distributions.get(engine, engine))
    except PackageNotFoundError:
        return "unknown"
