"""Nova ML orchestrator: columnar extraction, worker execution, and registry lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4

import asyncmy.cursors
import pyarrow as pa

from app.core.config import settings
from app.modules.ml_engine.artifacts.serializer import serialize_bundle
from app.modules.ml_engine.artifacts.store import ArtifactStore, ObjectArtifactStore
from app.modules.ml_engine.data.datasource import TrainingDataSource, collect_bounded
from app.modules.ml_engine.data.mysql_fallback import (
    ExistingConnectionBatchDataSource,
    PreferredDataSource,
)
from app.modules.ml_engine.engines.anomaly import train_anomaly
from app.modules.ml_engine.engines.clustering import train_clustering
from app.modules.ml_engine.engines.forecast import train_forecast
from app.modules.ml_engine.engines.tabular import train_tabular
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry, EphemeralRunCache
from app.modules.ml_engine.ephemeral.fingerprint import execution_fingerprint
from app.modules.ml_engine.execution.budgets import budget_for
from app.modules.ml_engine.execution.job_runner import MLJobRunner
from app.modules.ml_engine.registry.repository import (
    ModelRegistryRepository,
    model_registry_repository,
)
from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
from app.modules.ml_engine.spec import (
    MLExecutionSpec,
    MLMode,
    MLRunResult,
    MLSecurityContext,
    MLTask,
)
from app.modules.query.sql_pipeline import (
    guard_user_statement,
    prepare_stage_sql,
    redact_for_output,
)

logger = logging.getLogger(__name__)


def _train_worker(table: pa.Table, spec: MLExecutionSpec):
    """Picklable worker entry point; no credentials or database handles cross it."""
    if spec.task in {MLTask.CLASSIFICATION, MLTask.REGRESSION}:
        return train_tabular(table, spec)
    if spec.task is MLTask.FORECAST:
        return train_forecast(table, spec)
    if spec.task is MLTask.ANOMALY_DETECTION:
        return train_anomaly(table, spec)
    if spec.task is MLTask.CLUSTERING:
        return train_clustering(table, spec)
    raise ValueError(f"Unsupported ML task: {spec.task}")


class MLEngineService:
    """One bounded orchestration path for SQL DDL, HTTP, SQL prediction, and agents."""

    def __init__(
        self,
        *,
        data_source: TrainingDataSource | None = None,
        job_runner: MLJobRunner | None = None,
        artifact_store: ArtifactStore | None = None,
        repository: ModelRegistryRepository | None = None,
        runtime: ModelRuntime | None = None,
        ephemeral_cache: EphemeralRunCache | None = None,
    ) -> None:
        self.data_source = data_source or PreferredDataSource()
        self.job_runner = job_runner or MLJobRunner()
        self.artifact_store = artifact_store or ObjectArtifactStore()
        self.repository = repository or model_registry_repository
        self.runtime = runtime or ModelRuntime(
            repository=self.repository, store=self.artifact_store
        )
        self.ephemeral_cache = ephemeral_cache or EphemeralRunCache(
            settings.ML_EPHEMERAL_TTL_SECONDS
        )

    async def execute(self, spec: MLExecutionSpec) -> MLRunResult:
        spec.validate()
        budget = spec.budget or budget_for(spec.mode)
        spec = replace(spec, budget=budget)
        fingerprint = execution_fingerprint(spec)
        reusable = bool(spec.parameters.get("data_freshness_token"))
        if not spec.persist and reusable:
            cached = self.ephemeral_cache.by_fingerprint(spec.security.scope_key, fingerprint)
            if cached is not None:
                return self._result_from_entry(cached, spec, cache_hit=True)

        run_id = str(uuid4())
        started = time.perf_counter()
        telemetry: dict[str, Any] = {
            "run_id": run_id,
            "task": spec.task.value,
            "mode": spec.mode.value,
            "user_scope": spec.security.scope_key,
            "cache_hit": False,
        }
        model_id = None
        version = None
        artifact_uri = None
        version_registered = False
        try:
            engine_sql = await self._prepare_user_sql(spec.input_sql, spec.security)
            extracted = await collect_bounded(self.data_source, engine_sql, spec.security, budget)
            telemetry.update(
                {
                    "rows_read": extracted.metrics.rows_read,
                    "bytes_read": extracted.metrics.bytes_read,
                    "arrow_batches_read": extracted.metrics.batches_read,
                    "extraction_duration_ms": extracted.metrics.duration_ms,
                    "transport": extracted.metrics.transport,
                }
            )
            worker_spec = replace(
                spec,
                security=MLSecurityContext(username="ml-worker", password=""),
                input_sql="[prepared by orchestrator]",
            )
            training_started = time.perf_counter()
            output = await self.job_runner.run(
                _train_worker,
                extracted.table,
                worker_spec,
                timeout_seconds=budget.timeout_seconds,
            )
            telemetry["training_duration_ms"] = round(
                (time.perf_counter() - training_started) * 1000, 3
            )
            telemetry.update(
                {
                    "selected_engine": output.engine,
                    "selected_algorithm": output.algorithm,
                    "validation_metric": output.metrics.get("validation_score")
                    or output.metrics.get("validation_mae")
                    or output.metrics.get("silhouette"),
                }
            )
            payload, checksum = serialize_bundle(output.bundle)
            telemetry["artifact_size"] = len(payload)
            if spec.persist:
                model_id, version = await self.repository.reserve_version(
                    replace(spec, input_sql=redact_for_output(spec.input_sql)),
                    feature_columns=output.feature_columns,
                )
                artifact_uri = await asyncio.to_thread(
                    self.artifact_store.put,
                    self._artifact_key(spec, model_id, version),
                    payload,
                )
                await self.repository.register_version(
                    model_id=model_id,
                    version=version,
                    spec=spec,
                    output=output,
                    artifact_uri=artifact_uri,
                    artifact_sha256=checksum,
                    artifact_size=len(payload),
                    training_duration_ms=int(telemetry["training_duration_ms"]),
                )
                version_registered = True
            else:
                self.ephemeral_cache.put(
                    EphemeralEntry(
                        run_id=run_id,
                        fingerprint=fingerprint,
                        scope_key=spec.security.scope_key,
                        output=output,
                        artifact_payload=payload,
                        artifact_sha256=checksum,
                        expires_at=time.monotonic() + settings.ML_EPHEMERAL_TTL_SECONDS,
                    )
                )
            telemetry["total_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            await self._record_run_best_effort(
                run_id=run_id,
                spec=spec,
                status="succeeded",
                fingerprint=fingerprint,
                telemetry=telemetry,
                model_id=model_id,
                version=version,
                artifact_uri=artifact_uri,
            )
            return MLRunResult(
                run_id=run_id,
                task=spec.task.value,
                mode=spec.mode.value,
                status="succeeded",
                selected_engine=output.engine,
                selected_algorithm=output.algorithm,
                training_rows=output.training_rows,
                feature_columns=output.feature_columns,
                metrics=output.metrics,
                results=output.results,
                model_id=model_id,
                version=version,
                artifact_uri=artifact_uri,
                telemetry=telemetry,
                message=(
                    f"Model version {version} registered"
                    if spec.persist
                    else "Ephemeral ML run completed"
                ),
            )
        except Exception as exc:
            if spec.persist and not version_registered and model_id and version:
                await self._abort_persistence_best_effort(
                    model_id=model_id,
                    version=version,
                    artifact_uri=artifact_uri,
                )
            telemetry["total_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            telemetry["error_class"] = type(exc).__name__
            await self._record_run_best_effort(
                run_id=run_id,
                spec=spec,
                status="failed",
                fingerprint=fingerprint,
                telemetry=telemetry,
                error=exc,
            )
            raise

    async def promote(
        self,
        run_id: str,
        *,
        model_name: str,
        security: MLSecurityContext,
    ) -> MLRunResult:
        entry = self.ephemeral_cache.by_run(run_id, security.scope_key)
        if entry is None:
            raise ValueError("Ephemeral ML run was not found, expired, or belongs to another scope")
        output = entry.output
        spec = MLExecutionSpec(
            task=MLTask(output.bundle["task"]),
            input_sql="[promoted ephemeral run]",
            security=security,
            persist=True,
            model_name=model_name,
            feature_columns=tuple(output.feature_columns),
            target_column=output.bundle.get("target_column"),
        )
        model_id = None
        version = None
        artifact_uri = None
        try:
            model_id, version = await self.repository.reserve_version(
                spec, feature_columns=output.feature_columns
            )
            artifact_uri = await asyncio.to_thread(
                self.artifact_store.put,
                self._artifact_key(spec, model_id, version),
                entry.artifact_payload,
            )
            await self.repository.register_version(
                model_id=model_id,
                version=version,
                spec=spec,
                output=output,
                artifact_uri=artifact_uri,
                artifact_sha256=entry.artifact_sha256,
                artifact_size=len(entry.artifact_payload),
                training_duration_ms=0,
            )
        except Exception:
            if model_id and version:
                await self._abort_persistence_best_effort(
                    model_id=model_id,
                    version=version,
                    artifact_uri=artifact_uri,
                )
            raise
        return MLRunResult(
            run_id=run_id,
            task=spec.task.value,
            mode=spec.mode.value,
            status="promoted",
            selected_engine=output.engine,
            selected_algorithm=output.algorithm,
            training_rows=output.training_rows,
            feature_columns=output.feature_columns,
            metrics=output.metrics,
            results=output.results,
            model_id=model_id,
            version=version,
            artifact_uri=artifact_uri,
            message=f"Promoted without retraining as {model_name} version {version}",
        )

    async def train_model(
        self,
        model_name: str,
        model_type: str,
        algorithm: str,
        training_sql: str,
        target_column: str | None,
        feature_columns: list[str] | None,
        hyperparameters: dict | None,
        test_size: float,
        database_name: str | None,
        created_by: str = "root",
        username: str | None = None,
        password: str | None = None,
        role: str | None = None,
        as_system: bool = False,
        timestamp_column: str | None = None,
        series_column: str | None = None,
        horizon: int | None = None,
        frequency: str | None = None,
        mode: str = "balanced",
    ) -> dict[str, Any]:
        del created_by
        if as_system:
            username = settings.STARROCKS_ROOT_USER
            password = settings.STARROCKS_ROOT_PASSWORD
        if username is None or password is None:
            raise ValueError("Training requires caller credentials")
        parameters = dict(hyperparameters or {})
        parameters.setdefault("test_size", test_size)
        result = await self.execute(
            MLExecutionSpec(
                task=MLTask(model_type),
                input_sql=training_sql,
                security=MLSecurityContext(
                    username=username,
                    password=password,
                    database=database_name,
                    role=role,
                ),
                mode=MLMode(mode),
                persist=True,
                model_name=model_name,
                algorithm=algorithm,
                feature_columns=tuple(feature_columns or ()),
                target_column=target_column,
                timestamp_column=timestamp_column,
                series_column=series_column,
                horizon=horizon,
                frequency=frequency,
                parameters=parameters,
            )
        )
        return {
            "model_id": result.model_id,
            "model_name": model_name,
            "model_type": model_type,
            "algorithm": result.selected_algorithm,
            "version": result.version,
            "status": result.status,
            "training_rows": result.training_rows,
            "feature_columns": result.feature_columns,
            "metrics": result.metrics,
            "message": result.message,
        }

    async def predict(
        self,
        model_alias: str,
        features: dict,
        *,
        owner_name: str = "root",
        database_name: str | None = None,
    ) -> dict[str, Any]:
        metadata, predictions, probabilities = await self.runtime.predict_alias(
            model_alias,
            pa.Table.from_pylist([features]),
            owner_name=owner_name,
            database_name=database_name,
        )
        return {
            "model_alias": model_alias,
            "model_name": metadata["model_name"],
            "prediction": predictions[0],
            "probability": probabilities[0] if probabilities else None,
            "model_version": metadata["version"],
        }

    async def predict_version(
        self,
        model_id: str,
        version: int,
        features: dict,
        *,
        owner_name: str,
        database_name: str | None,
    ) -> dict[str, Any]:
        metadata, predictions, probabilities = await self.runtime.predict_version(
            model_id,
            version,
            pa.Table.from_pylist([features]),
            owner_name=owner_name,
            database_name=database_name,
        )
        return {
            "model_id": model_id,
            "model_name": metadata["model_name"],
            "prediction": predictions[0],
            "probability": probabilities[0] if probabilities else None,
            "model_version": version,
        }

    async def batch_predict(
        self,
        model_alias: str,
        prediction_sql: str,
        database_name: str | None,
        username: str | None = None,
        password: str | None = None,
        role: str | None = None,
        as_system: bool = False,
        connection: Any | None = None,
    ) -> dict[str, Any]:
        # Guard SQL at the outer boundary even if caller credentials are
        # missing, so malformed requests cannot obscure a forbidden statement.
        security = MLSecurityContext(
            username=username or "",
            password=password or "",
            database=database_name,
            role=role,
        )
        engine_sql = await self._prepare_user_sql(prediction_sql, security)
        if as_system:
            username = settings.STARROCKS_ROOT_USER
            password = settings.STARROCKS_ROOT_PASSWORD
        if username is None or (password is None and connection is None):
            raise ValueError("Batch prediction requires caller credentials")
        security = MLSecurityContext(
            username=username, password=password or "", database=database_name, role=role
        )
        dataset = await collect_bounded(
            (
                ExistingConnectionBatchDataSource(connection)
                if connection is not None
                else self.data_source
            ),
            engine_sql,
            security,
            budget_for(MLMode.INTERACTIVE),
        )
        metadata, predictions, _ = await self.runtime.predict_alias(
            model_alias,
            dataset.table,
            owner_name=username,
            database_name=database_name,
        )
        rows = dataset.table.to_pylist()
        for row, prediction in zip(rows, predictions, strict=False):
            row["prediction"] = prediction
        return {
            "model_alias": model_alias,
            "model_name": metadata["model_name"],
            "predictions": rows,
            "total_rows": len(rows),
        }

    async def create_alias(
        self,
        alias_name: str,
        model_id: str,
        version: int,
        *,
        owner_name: str = "root",
        database_name: str | None = None,
    ) -> dict[str, Any]:
        return await self.repository.set_alias(
            alias_name,
            model_id,
            version,
            owner_name=owner_name,
            database_name=database_name,
        )

    async def _prepare_user_sql(
        self,
        sql: str,
        security: MLSecurityContext | None = None,
        *,
        database_name: str | None = None,
        what: str | None = None,
    ) -> str:
        del what
        guard_user_statement(sql)
        security = security or MLSecurityContext(username="", password="", database=database_name)
        if security.schema is None:
            stage_configs = await self._load_stage_configs(security.database)
        else:
            stage_configs = await self._load_stage_configs(
                security.database, schema_name=security.schema
            )
        prepared = await prepare_stage_sql(sql, stage_configs=stage_configs)
        return prepared.engine_sql

    async def _load_stage_configs(
        self,
        database_name: str | None,
        schema_name: str | None = None,
    ) -> dict[str, Any]:
        """Compatibility seam for SQL translation tests and extensions."""
        from app.modules.query.service import query_service

        return await query_service._load_stage_configs(database_name, schema_name)

    @staticmethod
    def _redacted_user_sql(sql: str) -> str:
        """Return the only SQL representation allowed in metadata and logs."""
        return redact_for_output(sql)

    async def _record_run_best_effort(self, **kwargs: Any) -> None:
        try:
            await self.repository.record_run(**kwargs)
        except Exception:
            logger.warning("Could not persist ML run telemetry", exc_info=True)

    async def _abort_persistence_best_effort(
        self,
        *,
        model_id: str,
        version: int,
        artifact_uri: str | None,
    ) -> None:
        try:
            await self.repository.abort_version(model_id, version)
        except Exception:
            logger.warning("Could not roll back ML version reservation", exc_info=True)
        if artifact_uri:
            try:
                await asyncio.to_thread(self.artifact_store.delete, artifact_uri)
            except Exception:
                logger.warning("Could not remove partial ML artifact", exc_info=True)

    async def _connect(self):
        """Compatibility hook for callers that extend registry operations."""
        return await self.repository._connect()

    @staticmethod
    def _artifact_key(spec: MLExecutionSpec, model_id: str, version: int) -> str:
        safe_scope = re.sub(r"[^A-Za-z0-9_.-]", "_", spec.security.scope_key)
        return (
            f"{settings.ML_ARTIFACT_PREFIX.strip('/')}/{safe_scope}/"
            f"{model_id}/v{version}/model.joblib"
        )

    @staticmethod
    def _result_from_entry(
        entry: EphemeralEntry, spec: MLExecutionSpec, *, cache_hit: bool
    ) -> MLRunResult:
        output = entry.output
        return MLRunResult(
            run_id=entry.run_id,
            task=spec.task.value,
            mode=spec.mode.value,
            status="succeeded",
            selected_engine=output.engine,
            selected_algorithm=output.algorithm,
            training_rows=output.training_rows,
            feature_columns=output.feature_columns,
            metrics=output.metrics,
            results=output.results,
            cache_hit=cache_hit,
            telemetry={"cache_hit": cache_hit},
            message="Reused a fresh equivalent ephemeral run",
        )

    async def list_models(
        self, *, owner_name: str, database_name: str | None = None
    ) -> list[dict[str, Any]]:
        conn = await self.repository._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT m.*, v.version AS latest_version, v.status AS latest_status, "
                    "v.metrics AS latest_metrics, v.training_rows, v.algorithm AS algorithm "
                    "FROM NOVA_SYSTEM.ML_MODELS m LEFT JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v "
                    "ON m.model_id=v.model_id AND v.version=m.current_version "
                    "WHERE m.created_by=%s "
                    + (
                        "AND COALESCE(m.database_name, '')=COALESCE(%s, '') "
                        if database_name is not None
                        else ""
                    )
                    + "ORDER BY m.updated_at DESC",
                    (owner_name, database_name) if database_name is not None else (owner_name,),
                )
                rows = await cursor.fetchall()
        finally:
            conn.close()
        return [self._deserialize_metadata(row) for row in rows]

    async def get_model(
        self,
        model_id: str,
        *,
        owner_name: str,
        database_name: str | None = None,
    ) -> dict[str, Any] | None:
        models = [
            item
            for item in await self.list_models(
                owner_name=owner_name, database_name=database_name
            )
            if item["model_id"] == model_id
        ]
        if not models:
            return None
        conn = await self.repository._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT version,status,training_rows,metrics,artifact_uri,artifact_size,"
                    "framework,algorithm,created_at,created_by FROM "
                    "NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id=%s ORDER BY version DESC",
                    (model_id,),
                )
                versions = [self._deserialize_metadata(row) for row in await cursor.fetchall()]
        finally:
            conn.close()
        return {"model": models[0], "versions": versions}

    async def delete_model(
        self,
        model_id: str,
        *,
        owner_name: str,
        database_name: str | None = None,
    ) -> dict[str, Any]:
        detail = await self.get_model(
            model_id, owner_name=owner_name, database_name=database_name
        )
        if detail is None:
            raise ValueError("Model not found in this scope")
        artifact_uris = [
            version.get("artifact_uri")
            for version in detail["versions"]
            if version.get("artifact_uri")
        ]
        conn = await self.repository._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_ALIASES WHERE model_id=%s", (model_id,)
                )
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_VERSIONS WHERE model_id=%s", (model_id,)
                )
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODELS WHERE model_id=%s", (model_id,)
                )
        finally:
            conn.close()
        for artifact_uri in artifact_uris:
            try:
                await asyncio.to_thread(self.artifact_store.delete, artifact_uri)
            except Exception:
                logger.warning(
                    "Unable to delete model artifact for model %s",
                    model_id,
                    exc_info=True,
                )
        self.runtime.cache.invalidate()
        return {"model_id": model_id, "deleted": True, "message": "Model deleted"}

    async def list_aliases(
        self, *, owner_name: str = "root", database_name: str | None = None
    ) -> list[dict[str, Any]]:
        conn = await self.repository._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT a.alias_name,a.model_id,a.version,a.created_at,m.model_name "
                    "FROM NOVA_SYSTEM.ML_MODEL_ALIASES a LEFT JOIN NOVA_SYSTEM.ML_MODELS m "
                    "ON m.model_id=a.model_id WHERE a.owner_name=%s AND a.database_name=%s "
                    "ORDER BY a.alias_name",
                    (owner_name, database_name or ""),
                )
                return [self._deserialize_metadata(row) for row in await cursor.fetchall()]
        finally:
            conn.close()

    async def delete_alias(
        self,
        alias_name: str,
        *,
        owner_name: str = "root",
        database_name: str | None = None,
    ) -> dict[str, Any]:
        conn = await self.repository._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_ALIASES WHERE alias_name=%s "
                    "AND owner_name=%s AND database_name=%s",
                    (alias_name, owner_name, database_name or ""),
                )
        finally:
            conn.close()
        return {"alias_name": alias_name, "deleted": True}

    @staticmethod
    def _deserialize_metadata(row: dict) -> dict[str, Any]:
        result = dict(row)
        for field in ("feature_columns", "hyperparameters", "metrics", "latest_metrics"):
            if isinstance(result.get(field), str):
                with contextlib.suppress(json.JSONDecodeError):
                    result[field] = json.loads(result[field])
        for key, value in list(result.items()):
            if isinstance(value, datetime):
                result[key] = value.isoformat()
        return result


ml_engine_service = MLEngineService()
