"""Nova ML orchestrator: columnar extraction, worker execution, and registry lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import uuid4

import asyncmy.cursors
import pyarrow as pa

from app.core.config import settings
from app.core.security import encrypt_password
from app.modules.ml_engine.artifacts.serializer import serialize_bundle
from app.modules.ml_engine.artifacts.store import ArtifactStore, ObjectArtifactStore
from app.modules.ml_engine.data.batches import inference_batches
from app.modules.ml_engine.data.datasource import TrainingDataSource, collect_bounded
from app.modules.ml_engine.data.mysql_fallback import (
    ExistingConnectionBatchDataSource,
    PreferredDataSource,
)
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.engines.lazy import (
    train_anomaly,
    train_clustering,
    train_forecast,
    train_tabular,
)
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry, EphemeralRunCache
from app.modules.ml_engine.ephemeral.fingerprint import execution_fingerprint
from app.modules.ml_engine.ephemeral.repository import EphemeralRepository, sanitized_spec
from app.modules.ml_engine.execution.budgets import budget_for
from app.modules.ml_engine.execution.deadline import ExecutionDeadline, current_stage
from app.modules.ml_engine.execution.job_runner import MLJobRunner
from app.modules.ml_engine.execution.worker import MLWorkerJob, MLWorkerSecurity
from app.modules.ml_engine.registry.repository import (
    ModelRegistryRepository,
    alias_database_scope,
    model_registry_repository,
)
from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
from app.modules.ml_engine.spec import (
    MLExecutionSpec,
    MLExecutionTimeout,
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
        ephemeral_repository: EphemeralRepository | None = None,
    ) -> None:
        self.data_source = data_source or PreferredDataSource()
        self.job_runner = job_runner or MLJobRunner()
        self.artifact_store = artifact_store or ObjectArtifactStore()
        self.repository = repository or model_registry_repository
        self.runtime = runtime or ModelRuntime(
            repository=self.repository, store=self.artifact_store
        )
        self.ephemeral_cache = ephemeral_cache or EphemeralRunCache(
            settings.ML_EPHEMERAL_TTL_SECONDS,
            max_entries=settings.ML_EPHEMERAL_MAX_ENTRIES,
            max_memory_bytes=settings.ML_EPHEMERAL_MAX_MEMORY_BYTES,
        )
        self.ephemeral_repository = ephemeral_repository or EphemeralRepository(self.repository)

    async def execute(self, spec: MLExecutionSpec) -> MLRunResult:
        budget = spec.budget or budget_for(spec.mode)
        deadline_at = time.monotonic() + budget.timeout_seconds
        token = current_stage.set("startup")
        try:
            async with asyncio.timeout(budget.timeout_seconds):
                return await self._execute(replace(spec, budget=budget, deadline_at=deadline_at))
        except TimeoutError as exc:
            raise MLExecutionTimeout(current_stage.get()) from exc
        finally:
            current_stage.reset(token)

    async def _execute(self, spec: MLExecutionSpec) -> MLRunResult:
        spec.validate()
        budget = spec.budget or budget_for(spec.mode)
        spec = replace(spec, budget=budget)
        fingerprint = execution_fingerprint(spec)
        reusable = bool(spec.parameters.get("data_freshness_token"))
        if not spec.persist and reusable:
            cached = self.ephemeral_cache.by_fingerprint(spec.security.scope_key, fingerprint)
            if cached is None:
                cached = await self.ephemeral_repository.by_fingerprint(
                    fingerprint, spec.security.scope_key
                )
            if cached is not None:
                return self._result_from_entry(cached, spec, cache_hit=True)

        run_id = str(uuid4())
        started = time.perf_counter()
        assert spec.deadline_at is not None
        deadline = ExecutionDeadline(spec.deadline_at)
        telemetry: dict[str, Any] = {
            "run_id": run_id,
            "task": spec.task.value,
            "mode": spec.mode.value,
            "user_scope": spec.security.scope_key,
            "cache_hit": False,
            "ephemeral_cache_hit": False,
            "model_cache_hit": False,
        }
        model_id = None
        version = None
        artifact_uri = None
        version_registered = False
        try:
            if spec.persist:
                model_id, version = await self.repository.reserve_version(
                    replace(spec, input_sql=redact_for_output(spec.input_sql)),
                    feature_columns=list(spec.feature_columns),
                )
                artifact_key = self._artifact_key(spec, model_id, version)
            else:
                artifact_key = self._run_artifact_key(spec, run_id)
            await self._lease_upload(
                run_id,
                spec.security,
                self.artifact_store.uri_for_key(artifact_key),
                deadline,
                model_id=model_id,
                version=version,
            )
            deadline.remaining("extraction")
            engine_sql = await self._prepare_user_sql(spec.input_sql, spec.security)
            worker_result = None
            training_started = time.perf_counter()
            if (
                getattr(self.job_runner, "worker_direct", False)
                and type(self.data_source) is not PreferredDataSource
            ):
                raise ValueError("Worker-direct execution requires a worker-owned data source")
            if (
                getattr(self.job_runner, "worker_direct", False)
                and type(self.data_source) is PreferredDataSource
            ):
                # The production boundary carries only a job descriptor. The
                # caller password is encrypted before multiprocessing pickles
                # the payload and is decrypted only inside the child.
                protected_spec = replace(
                    spec,
                    security=replace(spec.security, password=""),
                    input_sql="[prepared by orchestrator]",
                )
                worker_result = await self.job_runner.run_job(
                    MLWorkerJob(
                        run_id=run_id,
                        spec=protected_spec,
                        normalized_input_sql=encrypt_password(engine_sql),
                        security=MLWorkerSecurity(
                            username=spec.security.username,
                            encrypted_password=encrypt_password(spec.security.password),
                            database=spec.security.database,
                            schema=spec.security.schema,
                            role=spec.security.role,
                            tenant=spec.security.tenant,
                            security_context_version=spec.security.security_context_version,
                        ),
                        dispatched_at=time.perf_counter(),
                        artifact_key=artifact_key,
                        deadline_at=deadline.expires_at,
                        encrypted_sql=True,
                    ),
                    timeout_seconds=deadline.remaining("extraction"),
                )
                output = worker_result.output
                extraction = worker_result.extraction
                telemetry["ipc_mode"] = "worker_direct"
                telemetry["dataset_ipc_bytes"] = 0
                telemetry["trained_model_ipc_bytes"] = 0
                telemetry["worker_result_ipc_bytes"] = worker_result.worker_result_ipc_bytes
                telemetry["worker_startup_seconds"] = worker_result.worker_startup_seconds
                telemetry["training_seconds"] = worker_result.training_seconds
                telemetry["worker_peak_rss_bytes"] = worker_result.worker_peak_rss_bytes
            else:
                # Explicit test/embedded seam. Production MLJobRunner never
                # follows this path, which keeps custom in-memory sources usable
                # without teaching a child process how to reconstruct them.
                extracted = await collect_bounded(
                    self.data_source, engine_sql, spec.security, budget
                )
                extraction = extracted.metrics
                output = await self.job_runner.run(
                    _train_worker,
                    extracted.table,
                    replace(spec, input_sql="[prepared by orchestrator]"),
                    timeout_seconds=deadline.remaining("training"),
                )
                telemetry["ipc_mode"] = "embedded_columnar"
                telemetry["dataset_ipc_bytes"] = extracted.table.nbytes
                telemetry["worker_startup_seconds"] = 0.0
                telemetry["training_seconds"] = time.perf_counter() - training_started
                telemetry["worker_peak_rss_bytes"] = None
            telemetry.update(
                {
                    "extraction_rows": extraction.rows_read,
                    "extraction_bytes": extraction.bytes_read,
                    "record_batches": extraction.batches_read,
                    "largest_batch_bytes": extraction.largest_batch_bytes,
                    "final_materialized_bytes": extraction.final_materialized_bytes,
                    "extraction_seconds": extraction.duration_ms / 1000,
                    "queue_wait_time": getattr(extraction, "queue_wait_seconds", 0.0),
                    "data_conversion_duration": getattr(
                        extraction, "data_conversion_duration", 0.0
                    ),
                    "transport": extraction.transport,
                }
            )
            telemetry["training_duration_ms"] = round(
                float(telemetry["training_seconds"]) * 1000, 3
            )
            telemetry.update(
                {
                    "selected_engine": output.engine,
                    "selected_algorithm": output.algorithm,
                    "validation_metric": next(
                        (
                            output.metrics[key]
                            for key in ("validation_score", "validation_mae", "silhouette")
                            if output.metrics.get(key) is not None
                        ),
                        None,
                    ),
                    "arrow_to_pandas_seconds": output.metrics.get("arrow_to_pandas_seconds", 0.0),
                    "arrow_to_numpy_seconds": output.metrics.get("arrow_to_numpy_seconds", 0.0),
                }
            )
            if worker_result is not None:
                if output.bundle or not worker_result.artifact_uri:
                    raise ValueError("ML worker must return a completed artifact descriptor")
                artifact_uri = worker_result.artifact_uri
                checksum = worker_result.artifact_sha256
                artifact_size = worker_result.artifact_size
                telemetry["serialization_seconds"] = worker_result.serialization_seconds
                telemetry["upload_seconds"] = worker_result.upload_seconds
                telemetry["remaining_before_training_ms"] = (
                    worker_result.remaining_before_training_seconds * 1000
                )
            else:
                serialization_started = time.perf_counter()
                payload, checksum = await asyncio.to_thread(serialize_bundle, output.bundle)
                telemetry["serialization_seconds"] = time.perf_counter() - serialization_started
                artifact_size = len(payload)
                upload_started = time.perf_counter()
                artifact_uri = await asyncio.to_thread(
                    self.artifact_store.put, artifact_key, payload
                )
                telemetry["upload_seconds"] = time.perf_counter() - upload_started
            telemetry["artifact_size"] = artifact_size
            telemetry["artifact_bytes"] = artifact_size
            telemetry["artifact_upload_bytes"] = artifact_size
            telemetry["artifact_checksum"] = checksum
            telemetry["configured_budget_ms"] = budget.timeout_seconds * 1000
            telemetry["transport_used"] = extraction.transport
            assert artifact_uri is not None
            if spec.persist:
                assert model_id is not None and version is not None
                deadline.remaining("registration")
                await self.repository.register_version(
                    model_id=model_id,
                    version=version,
                    spec=spec,
                    output=output,
                    artifact_uri=artifact_uri,
                    artifact_sha256=checksum,
                    artifact_size=artifact_size,
                    training_duration_ms=int(telemetry["training_duration_ms"]),
                )
                version_registered = True
                with contextlib.suppress(Exception):
                    await self.runtime.executor.run(
                        self.artifact_store.cleanup_upload, artifact_uri, keep_final=True
                    )
                    await self.ephemeral_repository.remove(run_id, spec.security.scope_key)
            else:
                cached_output = replace(output, bundle={}, results=output.results[:1000])
                entry = EphemeralEntry(
                    run_id=run_id,
                    fingerprint=fingerprint,
                    scope_key=spec.security.scope_key,
                    task=spec.task.value,
                    output=cached_output,
                    artifact_uri=artifact_uri,
                    artifact_sha256=checksum,
                    artifact_size=artifact_size,
                    expires_at=time.monotonic() + settings.ML_EPHEMERAL_TTL_SECONDS,
                    spec_snapshot=sanitized_spec(spec),
                )
                await self.ephemeral_repository.put(entry)
                self.ephemeral_cache.put(entry)
            telemetry["result_rows"] = (
                output.result_table.num_rows
                if output.result_table is not None
                else output.metrics.get("result_rows", len(output.results))
            )
            telemetry["result_bytes"] = (
                output.result_table.nbytes
                if output.result_table is not None
                else output.metrics.get("result_bytes")
            )
            telemetry["total_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            telemetry["lifecycle_state"] = "SUCCEEDED"
            telemetry["ephemeral_cache_hit"] = False
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
        except asyncio.CancelledError as exc:
            if spec.persist and not version_registered and model_id and version:
                await self._abort_persistence_best_effort(
                    model_id=model_id,
                    version=version,
                    artifact_uri=artifact_uri,
                )
            elif not spec.persist and artifact_uri:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.artifact_store.delete, artifact_uri)
            telemetry["lifecycle_state"] = "CANCELLED"
            telemetry["total_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            await self._record_run_best_effort(
                run_id=run_id,
                spec=spec,
                status="cancelled",
                fingerprint=fingerprint,
                telemetry=telemetry,
                model_id=model_id,
                version=version,
                artifact_uri=artifact_uri,
                error=exc,
            )
            raise
        except Exception as exc:
            if spec.persist and not version_registered and model_id and version:
                await self._abort_persistence_best_effort(
                    model_id=model_id,
                    version=version,
                    artifact_uri=artifact_uri,
                )
            elif not spec.persist and artifact_uri:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.artifact_store.delete, artifact_uri)
            telemetry["total_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            telemetry["error_class"] = type(exc).__name__
            telemetry["error_type"] = type(exc).__name__
            telemetry["timeout_stage"] = getattr(exc, "stage", None)
            telemetry["lifecycle_state"] = "FAILED"
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
        security.validate()
        budget = budget_for(MLMode.BEST)
        deadline = ExecutionDeadline(time.monotonic() + budget.timeout_seconds)
        try:
            async with asyncio.timeout(budget.timeout_seconds):
                return await self._promote(run_id, model_name, security, deadline)
        except TimeoutError as exc:
            raise MLExecutionTimeout("artifact_upload") from exc

    async def _promote(
        self,
        run_id: str,
        model_name: str,
        security: MLSecurityContext,
        deadline: ExecutionDeadline,
    ) -> MLRunResult:
        entry = await self.ephemeral_repository.get(run_id, security.scope_key)
        if entry is None:
            raise ValueError("Ephemeral ML run was not found, expired, or belongs to another scope")
        if entry.artifact_kind != "model":
            raise ValueError("Only a trained model run can be promoted")
        output = entry.output
        snapshot = dict(entry.spec_snapshot)
        snapshot.update(
            task=MLTask(entry.task),
            mode=MLMode(snapshot.get("mode", "interactive")),
            persist=True,
            model_name=model_name,
            security=security,
        )
        spec = MLExecutionSpec(**snapshot)
        model_id = None
        version = None
        artifact_uri = None
        registered = False
        lease_id = str(uuid4())
        try:
            model_id, version = await self.repository.reserve_version(
                spec, feature_columns=output.feature_columns
            )
            destination = self._artifact_key(spec, model_id, version)
            await self._lease_upload(
                lease_id,
                security,
                self.artifact_store.uri_for_key(destination),
                deadline,
                model_id=model_id,
                version=version,
            )
            deadline.remaining("artifact_upload")
            artifact_uri = await asyncio.to_thread(
                self.artifact_store.copy,
                entry.artifact_uri,
                destination,
                entry.artifact_sha256,
            )
            deadline.remaining("registration")
            await self.repository.register_version(
                model_id=model_id,
                version=version,
                spec=spec,
                output=output,
                artifact_uri=artifact_uri,
                artifact_sha256=entry.artifact_sha256,
                artifact_size=entry.artifact_size,
                training_duration_ms=0,
            )
            registered = True
            with contextlib.suppress(Exception):
                await self.ephemeral_repository.remove(lease_id, security.scope_key)
        except BaseException:
            if not registered and model_id and version:
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
        tenant: str = "default",
    ) -> dict[str, Any]:
        del created_by
        if as_system:
            if settings.RANGER_ENABLED:
                raise ValueError("System identity cannot train on user data")
            username = settings.STARROCKS_ROOT_USER
            password = settings.STARROCKS_ROOT_PASSWORD
        if username is None or password is None:
            raise ValueError("Training requires caller credentials")
        parameters = {
            "test_size": test_size,
            "estimator_parameters": dict(hyperparameters or {}),
        }
        result = await self.execute(
            MLExecutionSpec(
                task=MLTask(model_type),
                input_sql=training_sql,
                security=MLSecurityContext(
                    username=username,
                    password=password,
                    database=database_name,
                    role=role,
                    tenant=tenant,
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
        tenant: str = "default",
    ) -> dict[str, Any]:
        metadata, predictions, probabilities = await self.runtime.predict_alias(
            model_alias,
            pa.Table.from_pylist([features]),
            owner_name=owner_name,
            database_name=database_name,
            **({"tenant": tenant} if tenant != "default" else {}),
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
        tenant: str = "default",
    ) -> dict[str, Any]:
        metadata, predictions, probabilities = await self.runtime.predict_version(
            model_id,
            version,
            pa.Table.from_pylist([features]),
            owner_name=owner_name,
            database_name=database_name,
            **({"tenant": tenant} if tenant != "default" else {}),
        )
        return {
            "model_id": model_id,
            "model_name": metadata["model_name"],
            "prediction": predictions[0],
            "probability": probabilities[0] if probabilities else None,
            "model_version": version,
        }

    async def forecast_alias(
        self,
        model_alias: str,
        horizon: int,
        *,
        owner_name: str,
        database_name: str | None = None,
        level: int = 95,
        series: str | None = None,
        tenant: str = "default",
    ) -> dict[str, Any]:
        metadata, table = await self.runtime.forecast_alias(
            model_alias,
            horizon,
            owner_name=owner_name,
            database_name=database_name,
            level=level,
            series=series,
            **({"tenant": tenant} if tenant != "default" else {}),
        )
        return {
            "model_alias": model_alias,
            "model_name": metadata["model_name"],
            "model_version": metadata["version"],
            "forecast": table.to_pylist(),
        }

    async def forecast_version(
        self,
        model_id: str,
        version: int,
        horizon: int,
        *,
        owner_name: str,
        database_name: str | None = None,
        level: int = 95,
        series: str | None = None,
        tenant: str = "default",
    ) -> dict[str, Any]:
        metadata, table = await self.runtime.forecast_version(
            model_id,
            version,
            horizon,
            owner_name=owner_name,
            database_name=database_name,
            level=level,
            series=series,
            **({"tenant": tenant} if tenant != "default" else {}),
        )
        return {
            "model_id": model_id,
            "model_name": metadata["model_name"],
            "model_version": version,
            "forecast": table.to_pylist(),
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
        tenant: str = "default",
    ) -> dict[str, Any]:
        # Guard SQL at the outer boundary even if caller credentials are
        # missing, so malformed requests cannot obscure a forbidden statement.
        security = MLSecurityContext(
            username=username or "",
            password=password or "",
            database=database_name,
            role=role,
            tenant=tenant,
        )
        engine_sql = await self._prepare_user_sql(prediction_sql, security)
        if as_system:
            if settings.RANGER_ENABLED:
                raise ValueError("System identity cannot run prediction on user data")
            username = settings.STARROCKS_ROOT_USER
            password = settings.STARROCKS_ROOT_PASSWORD
        if username is None or (password is None and connection is None):
            raise ValueError("Batch prediction requires caller credentials")
        security = MLSecurityContext(
            username=username,
            password=password or "",
            database=database_name,
            role=role,
            tenant=tenant,
        )
        dataset = await collect_bounded(
            (
                ExistingConnectionBatchDataSource(connection)
                if connection is not None
                else self.data_source
            ),
            engine_sql,
            security,
            replace(
                budget_for(MLMode.INTERACTIVE),
                max_rows=settings.ML_RESULT_INLINE_MAX_ROWS,
                max_bytes=settings.ML_RESULT_INLINE_MAX_BYTES,
            ),
        )
        # Keep the public JSON API on its established runtime seam. SQL
        # interception uses batch_predict_projected(), which remains Arrow
        # columnar through projection; this endpoint must also support custom
        # runtimes that implement the original predict_alias contract.
        metadata, predictions, probabilities = await self.runtime.predict_alias(
            model_alias,
            dataset.table,
            owner_name=username,
            database_name=database_name,
            **({"tenant": tenant} if tenant != "default" else {}),
        )
        rows = dataset.table.to_pylist()
        for index, row in enumerate(rows):
            row["prediction"] = predictions[index]
            if probabilities is not None:
                row["probability"] = probabilities[index]
        return {
            "model_alias": model_alias,
            "model_name": metadata["model_name"],
            "predictions": rows,
            "total_rows": len(rows),
        }

    async def batch_predict_projected(
        self,
        model_alias: str,
        prediction_sql: str,
        *,
        feature_source_columns: tuple[str, ...],
        prediction_index: int,
        prediction_name: str,
        database_name: str | None,
        username: str,
        password: str,
        role: str | None = None,
        connection: Any | None = None,
        max_rows: int | None = None,
        predictions: tuple | None = None,
        tenant: str = "default",
    ) -> tuple[dict[str, Any], pa.Table]:
        security = MLSecurityContext(
            username=username,
            password=password,
            database=database_name,
            role=role,
            tenant=tenant,
        )
        engine_sql = await self._prepare_user_sql(prediction_sql, security)
        budget = budget_for(MLMode.INTERACTIVE)
        budget = replace(
            budget,
            max_rows=min(
                budget.max_rows,
                max_rows or settings.ML_SQL_RESULT_MAX_ROWS,
                settings.ML_RESULT_INLINE_MAX_ROWS,
            ),
            max_bytes=min(budget.max_bytes, settings.ML_RESULT_INLINE_MAX_BYTES),
        )
        dataset = await collect_bounded(
            ExistingConnectionBatchDataSource(connection)
            if connection is not None
            else self.data_source,
            engine_sql,
            security,
            budget,
        )
        from app.common.ml_intercept import MLPredictExpression

        expressions = predictions or (
            MLPredictExpression(
                model_alias, (), feature_source_columns, prediction_index, prediction_name
            ),
        )
        hidden = list(dict.fromkeys(name for expr in expressions for name in expr.feature_columns))
        result = dataset.table.drop(hidden)
        metadata = {}
        for expression in expressions:
            metadata = await self.runtime.resolve_alias(
                expression.alias,
                owner_name=username,
                database_name=database_name,
                **({"tenant": tenant} if tenant != "default" else {}),
            )
            bundle = await self.runtime.load(metadata)
            expected = list(bundle.get("feature_columns") or [])
            if len(expected) != len(expression.feature_columns):
                raise ValueError(
                    f"Model expects {len(expected)} feature(s), but ML_PREDICT received "
                    f"{len(expression.feature_columns)}"
                )
            features = dataset.table.select(expression.feature_columns).rename_columns(expected)
            chunks = []
            for batch in inference_batches(features):
                predicted = await self.runtime.executor.run(
                    self.runtime.predict_bundle_table, bundle, pa.Table.from_batches([batch])
                )
                chunks.extend(predicted.column("prediction").chunks)
            result = result.add_column(
                expression.prediction_index, expression.prediction_name, pa.chunked_array(chunks)
            )
            if result.nbytes > settings.ML_RESULT_INLINE_MAX_BYTES:
                from app.modules.ml_engine.spec import DataBudgetExceeded

                raise DataBudgetExceeded("Prediction output exceeds the inline byte limit")
        return metadata, result

    async def materialize_prediction(
        self, model_alias: str, prediction_sql: str, security: MLSecurityContext
    ) -> dict[str, Any]:
        token = current_stage.set("startup")
        try:
            async with asyncio.timeout(budget_for(MLMode.BEST).timeout_seconds):
                return await self._materialize_prediction(model_alias, prediction_sql, security)
        except TimeoutError as exc:
            raise MLExecutionTimeout(current_stage.get()) from exc
        finally:
            current_stage.reset(token)

    async def _materialize_prediction(
        self, model_alias: str, prediction_sql: str, security: MLSecurityContext
    ) -> dict[str, Any]:
        from app.modules.ml_engine.execution.inference_worker import (
            MLInferenceJob,
            execute_inference_job,
        )

        security.validate()
        budget = budget_for(MLMode.BEST)
        deadline = ExecutionDeadline(time.monotonic() + budget.timeout_seconds)
        sql = await self._prepare_user_sql(prediction_sql, security)
        metadata = await self.runtime.resolve_alias(
            model_alias,
            owner_name=security.username,
            database_name=security.database,
            tenant=security.tenant,
        )
        run_id = str(uuid4())
        scope = hashlib.sha256(security.scope_key.encode()).hexdigest()
        prefix = f"{settings.ML_ARTIFACT_PREFIX.strip('/')}/results/{scope}/{run_id}"
        await self._lease_upload(
            run_id,
            security,
            self.artifact_store.uri_for_key(f"{prefix}/manifest.json"),
            deadline,
            result=True,
        )
        result = await self.job_runner.run(
            execute_inference_job,
            MLInferenceJob(
                run_id=run_id,
                encrypted_sql=encrypt_password(sql),
                security=MLWorkerSecurity(
                    security.username,
                    encrypt_password(security.password),
                    security.database,
                    security.schema,
                    security.role,
                    security.tenant,
                    security.security_context_version,
                ),
                model_metadata={key: metadata[key] for key in ("artifact_uri", "artifact_sha256")},
                result_prefix=prefix,
                deadline_at=deadline.expires_at,
                max_rows=budget.max_rows,
                max_bytes=budget.max_bytes,
            ),
            timeout_seconds=deadline.remaining("inference"),
        )
        result["scope_key"] = security.scope_key
        await self.ephemeral_repository.put(
            EphemeralEntry(
                run_id=run_id,
                fingerprint="",
                scope_key=security.scope_key,
                output=TrainingOutput({}, "inference", model_alias, result, [], 0),
                artifact_sha256="",
                expires_at=time.monotonic() + settings.ML_EPHEMERAL_TTL_SECONDS,
                artifact_uri=result["result_uri"],
                artifact_size=result["result_bytes"],
                artifact_kind="result",
            )
        )
        await self.repository.record_run(
            run_id=run_id,
            spec=MLExecutionSpec(
                task=MLTask(metadata["model_type"]),
                input_sql=redact_for_output(prediction_sql),
                security=security,
                mode=MLMode.BEST,
            ),
            status="succeeded",
            fingerprint="",
            telemetry=result,
        )
        return {
            "result_id": run_id,
            "total_rows": result["result_rows"],
            "parts": result["result_parts"],
            "telemetry": {
                key: value
                for key, value in result.items()
                if key not in {"result_uri", "scope_key"}
            },
        }

    async def result_page(
        self, run_id: str, part: int, security: MLSecurityContext
    ) -> dict[str, Any]:
        import io

        import pyarrow.parquet as pq

        security.validate()
        entry = await self.ephemeral_repository.get(run_id, security.scope_key)
        if entry is None or entry.artifact_kind != "result":
            raise ValueError("Result was not found in this scope or has expired")
        manifest = json.loads(
            await self.runtime.executor.run(self.artifact_store.get, entry.artifact_uri)
        )
        if part < 0 or part >= len(manifest["parts"]):
            raise ValueError("Result part is outside the available range")
        payload = await self.runtime.executor.run(self.artifact_store.get, manifest["parts"][part])
        table = await self.runtime.executor.run(pq.read_table, io.BytesIO(payload))
        rows = await self.runtime.executor.run(table.to_pylist)
        return {
            "result_id": run_id,
            "part": part,
            "rows": rows,
            "next_part": part + 1 if part + 1 < len(manifest["parts"]) else None,
        }

    async def create_alias(
        self,
        alias_name: str,
        model_id: str,
        version: int,
        *,
        owner_name: str = "root",
        database_name: str | None = None,
        tenant: str = "default",
    ) -> dict[str, Any]:
        return await self.repository.set_alias(
            alias_name,
            model_id,
            version,
            owner_name=owner_name,
            database_name=database_name,
            **({"tenant": tenant} if tenant != "default" else {}),
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
        from app.modules.query.dialect.parser import parse_sql
        from app.modules.query.service import query_service

        parsed = parse_sql(sql)
        if parsed.stage_refs:
            parsed, configs = await query_service._resolve_stage_refs(
                parsed, database=security.database, schema=security.schema,
                username=security.username, password=security.password,
                role=security.role,
            )
            prepared = await prepare_stage_sql(
                sql, parsed=parsed, stage_configs_by_ref=configs
            )
        else:
            prepared = await prepare_stage_sql(sql)
        return prepared.engine_sql

    @staticmethod
    def _redacted_user_sql(sql: str) -> str:
        """Return the only SQL representation allowed in metadata and logs."""
        return redact_for_output(sql)

    async def _record_run_best_effort(self, **kwargs: Any) -> None:
        try:
            await self.repository.record_run(**kwargs)
        except Exception:
            logger.warning("Could not persist ML run telemetry")

    async def _abort_persistence_best_effort(
        self,
        *,
        model_id: str,
        version: int,
        artifact_uri: str | None,
    ) -> None:
        artifact_can_be_deleted = True
        try:
            result = await self.repository.abort_version(model_id, version)
            artifact_can_be_deleted = result is not False
        except Exception:
            logger.warning("Could not roll back ML version reservation")
        if artifact_uri and artifact_can_be_deleted:
            try:
                await asyncio.to_thread(self.artifact_store.delete, artifact_uri)
            except Exception:
                logger.warning("Could not remove partial ML artifact")

    async def _connect(self):
        """Compatibility hook for callers that extend registry operations."""
        return await self.repository._connect()

    async def cleanup_ephemeral(self) -> int:
        deleted = 0
        for entry in await self.ephemeral_repository.expired():
            try:
                if entry.artifact_kind in {"result", "pending_result"}:
                    await self.runtime.executor.run(
                        self.artifact_store.cleanup_result, entry.artifact_uri
                    )
                else:
                    ready = None
                    lease = entry.spec_snapshot
                    if entry.artifact_kind == "pending_model" and lease.get("model_id"):
                        ready = await self.repository.get_version(
                            lease["model_id"],
                            lease["version"],
                            owner_name=lease["username"],
                            database_name=lease["database"],
                            tenant=lease["tenant"],
                        )
                        if ready is None:
                            aborted = await self.repository.abort_version(
                                lease["model_id"], lease["version"]
                            )
                            if aborted is False:
                                ready = {}
                    await self.runtime.executor.run(
                        self.artifact_store.cleanup_upload,
                        entry.artifact_uri,
                        keep_final=ready is not None,
                    )
                await self.ephemeral_repository.delete_expired(entry)
                deleted += 1
            except Exception:
                logger.warning("Ephemeral cleanup failed for run %s", entry.run_id)
        return deleted

    async def _lease_upload(
        self,
        run_id: str,
        security: MLSecurityContext,
        uri: str,
        deadline: ExecutionDeadline,
        *,
        model_id: str | None = None,
        version: int | None = None,
        result: bool = False,
    ) -> None:
        # Persist intent before dispatch so process death leaves a discoverable destination.
        await self.ephemeral_repository.put(
            EphemeralEntry(
                run_id=run_id,
                fingerprint="",
                scope_key=security.scope_key,
                output=TrainingOutput({}, "pending", "", {}, [], 0),
                artifact_sha256="",
                artifact_uri=uri,
                expires_at=deadline.expires_at + 60,
                artifact_kind="pending_result" if result else "pending_model",
                spec_snapshot={
                    "model_id": model_id,
                    "version": version,
                    "username": security.username,
                    "database": security.database,
                    "tenant": security.tenant,
                },
            )
        )

    async def sweep_ephemeral(self) -> None:
        while True:
            try:
                await self.cleanup_ephemeral()
            except Exception:
                logger.warning("Could not read expired ML runs")
            await asyncio.sleep(settings.ML_EPHEMERAL_CLEANUP_INTERVAL_SECONDS)

    @staticmethod
    def _run_artifact_key(spec: MLExecutionSpec, run_id: str) -> str:
        scope = hashlib.sha256(spec.security.scope_key.encode()).hexdigest()
        kind = "models" if spec.persist else "ephemeral"
        return f"{settings.ML_ARTIFACT_PREFIX.strip('/')}/{kind}/{scope}/{run_id}/model.joblib"

    @staticmethod
    def _artifact_key(spec: MLExecutionSpec, model_id: str, version: int) -> str:
        safe_scope = hashlib.sha256(spec.security.scope_key.encode()).hexdigest()
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
            telemetry={
                "cache_hit": cache_hit,
                "ephemeral_cache_hit": cache_hit,
                "model_cache_hit": False,
                "lifecycle_state": "SUCCEEDED",
            },
            message="Reused a fresh equivalent ephemeral run",
        )

    async def list_models(
        self, *, owner_name: str, database_name: str | None = None, tenant: str = "default"
    ) -> list[dict[str, Any]]:
        conn = await self.repository._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT m.*, v.version AS latest_version, v.status AS latest_status, "
                    "v.metrics AS latest_metrics, v.training_rows, v.algorithm AS algorithm "
                    "FROM NOVA_SYSTEM.ML_MODELS m LEFT JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v "
                    "ON m.model_id=v.model_id AND v.version=m.current_version "
                    "WHERE m.created_by=%s AND m.tenant_name=%s "
                    + (
                        "AND COALESCE(m.database_name, '')=COALESCE(%s, '') "
                        if database_name is not None
                        else ""
                    )
                    + "ORDER BY m.updated_at DESC",
                    (owner_name, tenant, database_name)
                    if database_name is not None
                    else (owner_name, tenant),
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
        tenant: str = "default",
    ) -> dict[str, Any] | None:
        models = [
            item
            for item in await self.list_models(
                owner_name=owner_name, database_name=database_name, tenant=tenant
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
        tenant: str = "default",
    ) -> dict[str, Any]:
        detail = await self.get_model(
            model_id, owner_name=owner_name, database_name=database_name, tenant=tenant
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
        self, *, owner_name: str = "root", database_name: str | None = None, tenant: str = "default"
    ) -> list[dict[str, Any]]:
        conn = await self.repository._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT a.alias_name,a.model_id,a.version,a.created_at,m.model_name "
                    "FROM NOVA_SYSTEM.ML_MODEL_ALIASES a LEFT JOIN NOVA_SYSTEM.ML_MODELS m "
                    "ON m.model_id=a.model_id WHERE a.owner_name=%s AND a.database_name=%s "
                    "ORDER BY a.alias_name",
                    (owner_name, alias_database_scope(database_name, tenant)),
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
        tenant: str = "default",
    ) -> dict[str, Any]:
        conn = await self.repository._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_MODEL_ALIASES WHERE alias_name=%s "
                    "AND owner_name=%s AND database_name=%s",
                    (alias_name, owner_name, alias_database_scope(database_name, tenant)),
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
