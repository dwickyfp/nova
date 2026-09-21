"""Credential-free StarRocks model/run metadata repository."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any
from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings
from app.modules.ml_engine.spec import MLExecutionSpec
from app.modules.query.sql_pipeline import redact_for_output


class ModelRegistryRepository:
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
        """Resolve a stable logical model ID and atomically advance its version."""
        assert spec.model_name
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT model_id, current_version FROM NOVA_SYSTEM.ML_MODELS "
                    "WHERE model_name=%s AND created_by=%s AND "
                    "COALESCE(database_name, '')=COALESCE(%s, '') "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (spec.model_name, spec.security.username, spec.security.database),
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
                # This DELETE is a no-op on fresh Primary Key tables after the
                # lookup and makes upgraded legacy Duplicate Key tables obey the
                # same one-row logical-model invariant.
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODELS WHERE model_id=%s",
                    (model_id,),
                )
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
                        version,
                    ),
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
        conn = await self._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO NOVA_SYSTEM.ML_MODEL_VERSIONS "
                    "(model_id, version, status, training_rows, metrics, artifact_uri, "
                    "artifact_sha256, artifact_size, task, framework, framework_version, "
                    "algorithm, "
                    "feature_schema, training_duration_ms, model_binary, created_at, created_by) "
                    "VALUES (%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NOW(),%s)",
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
        finally:
            conn.close()

    async def abort_version(self, model_id: str, version: int) -> None:
        """Remove a partial registration and restore the last completed version."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_VERSIONS "
                    "WHERE model_id=%s AND version=%s",
                    (model_id, version),
                )
                await cursor.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version "
                    "FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id=%s",
                    (model_id,),
                )
                row = await cursor.fetchone()
                previous = int(row["version"] or 0)
                if previous:
                    await cursor.execute(
                        "UPDATE NOVA_SYSTEM.ML_MODELS SET current_version=%s,updated_at=NOW() "
                        "WHERE model_id=%s AND current_version=%s",
                        (previous, model_id, version),
                    )
                else:
                    await cursor.execute(
                        "DELETE FROM NOVA_SYSTEM.ML_MODELS "
                        "WHERE model_id=%s AND current_version=%s",
                        (model_id, version),
                    )
        finally:
            conn.close()

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
                    "WHERE a.alias_name=%s AND a.owner_name=%s AND a.database_name=%s",
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
                    "AND COALESCE(m.database_name, '')=COALESCE(%s, '')",
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
