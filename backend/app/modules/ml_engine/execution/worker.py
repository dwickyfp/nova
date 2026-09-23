"""Small, credential-protected job contract for the ML process boundary."""

from __future__ import annotations

import asyncio
import pickle
import resource
import sys
import time
from dataclasses import dataclass, replace

from app.core.config import settings
from app.core.security import decrypt_password
from app.modules.ml_engine.artifacts.serializer import serialize_bundle
from app.modules.ml_engine.artifacts.store import ObjectArtifactStore
from app.modules.ml_engine.data.datasource import ExtractionMetrics, collect_bounded
from app.modules.ml_engine.data.mysql_fallback import PreferredDataSource
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.engines.lazy import (
    train_anomaly,
    train_clustering,
    train_forecast,
    train_tabular,
)
from app.modules.ml_engine.execution.deadline import ExecutionDeadline
from app.modules.ml_engine.registry.repository import _framework_version
from app.modules.ml_engine.spec import (
    DataBudgetExceeded,
    MLExecutionSpec,
    MLSecurityContext,
    MLTask,
)


@dataclass(frozen=True)
class MLWorkerSecurity:
    """The caller identity sent to a child without a plaintext password."""

    username: str
    encrypted_password: str
    database: str | None
    schema: str | None
    role: str | None
    tenant: str
    security_context_version: int = 1


@dataclass(frozen=True)
class MLWorkerJob:
    run_id: str
    spec: MLExecutionSpec
    normalized_input_sql: str
    security: MLWorkerSecurity
    dispatched_at: float = 0.0
    artifact_key: str = ""
    deadline_at: float = 0.0
    encrypted_sql: bool = False


@dataclass(frozen=True)
class MLWorkerResult:
    output: TrainingOutput
    extraction: ExtractionMetrics
    worker_startup_seconds: float = 0.0
    training_seconds: float = 0.0
    worker_peak_rss_bytes: int = 0
    artifact_uri: str = ""
    artifact_sha256: str = ""
    artifact_size: int = 0
    serialization_seconds: float = 0.0
    upload_seconds: float = 0.0
    worker_result_ipc_bytes: int = 0
    remaining_before_training_seconds: float = 0.0


def execute_worker_job(job: MLWorkerJob) -> MLWorkerResult:
    """Fetch and materialize data inside the worker, then train there.

    Full-dataset engines still require one Arrow-to-pandas/NumPy conversion,
    but the API process never owns the full input table.
    """
    worker_started = time.perf_counter()
    security = MLSecurityContext(
        username=job.security.username,
        password=decrypt_password(job.security.encrypted_password),
        database=job.security.database,
        schema=job.security.schema,
        role=job.security.role,
        tenant=job.security.tenant,
        security_context_version=job.security.security_context_version,
    )
    if job.spec.budget is None:  # fail closed at the process boundary
        raise ValueError("ML worker job is missing an execution budget")
    if not job.artifact_key:
        raise ValueError("ML worker job is missing an artifact destination")
    deadline = ExecutionDeadline(
        job.deadline_at or time.monotonic() + job.spec.budget.timeout_seconds
    )

    async def extract():
        from app.modules.ml_engine.spec import MLExecutionTimeout

        try:
            async with asyncio.timeout(deadline.remaining("extraction")):
                return await collect_bounded(
                    PreferredDataSource(),
                    decrypt_password(job.normalized_input_sql)
                    if job.encrypted_sql
                    else job.normalized_input_sql,
                    security,
                    job.spec.budget,
                )
        except TimeoutError as exc:
            raise MLExecutionTimeout("extraction") from exc

    dataset = asyncio.run(extract())
    remaining = deadline.remaining("training", reserve=settings.ML_FINALIZE_RESERVE_SECONDS)
    worker_spec = replace(
        job.spec,
        security=security,
        budget=replace(job.spec.budget, timeout_seconds=remaining),
        deadline_at=deadline.expires_at,
    )
    training_started = time.perf_counter()
    if worker_spec.task in {MLTask.CLASSIFICATION, MLTask.REGRESSION}:
        output = train_tabular(dataset.table, worker_spec)
    elif worker_spec.task is MLTask.FORECAST:
        output = train_forecast(dataset.table, worker_spec)
    elif worker_spec.task is MLTask.ANOMALY_DETECTION:
        output = train_anomaly(dataset.table, worker_spec)
    elif worker_spec.task is MLTask.CLUSTERING:
        output = train_clustering(dataset.table, worker_spec)
    else:  # pragma: no cover - enum validation prevents this
        raise ValueError(f"Unsupported ML task: {worker_spec.task}")
    if output.result_table is not None:
        output.metrics.setdefault("result_rows", output.result_table.num_rows)
        output.metrics.setdefault("result_bytes", output.result_table.nbytes)
        # Only the bounded preview crosses back to the API process. Large
        # columnar outputs need a result handle/transport, not process pickling.
        output = replace(output, result_table=None, results=output.results[:1000])
    else:
        output.metrics.setdefault("result_rows", len(output.results))
        output = replace(output, results=output.results[:1000])
    training_seconds = time.perf_counter() - training_started
    output.metrics.update(
        serialization_format="joblib",
        serialization_version=1,
        framework_version=_framework_version(output.engine),
    )
    deadline.remaining("training")
    deadline.remaining("serialization")
    serialization_started = time.perf_counter()
    payload, checksum = serialize_bundle(output.bundle)
    serialization_seconds = time.perf_counter() - serialization_started
    deadline.remaining("serialization")
    store = ObjectArtifactStore()
    upload_started = time.perf_counter()
    deadline.remaining("artifact_upload")
    uri = store.put(job.artifact_key, payload)
    try:
        deadline.remaining("artifact_upload")
    except Exception:
        store.delete(uri)
        raise
    result = MLWorkerResult(
        output=replace(output, bundle={}, result_table=None),
        extraction=dataset.metrics,
        worker_startup_seconds=(
            max(0.0, worker_started - job.dispatched_at) if job.dispatched_at else 0.0
        ),
        training_seconds=training_seconds,
        worker_peak_rss_bytes=_peak_rss_bytes(),
        artifact_uri=uri,
        artifact_sha256=checksum,
        artifact_size=len(payload),
        serialization_seconds=serialization_seconds,
        upload_seconds=time.perf_counter() - upload_started,
        remaining_before_training_seconds=remaining,
    )
    # Measure only the descriptor. No estimator or serialized artifact enters IPC.
    while True:
        size = len(pickle.dumps(result))
        if size > settings.ML_WORKER_RESULT_MAX_BYTES:
            raise DataBudgetExceeded("ML result descriptor exceeds the IPC metadata budget")
        if size == result.worker_result_ipc_bytes:
            return result
        result = replace(result, worker_result_ipc_bytes=size)


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)
