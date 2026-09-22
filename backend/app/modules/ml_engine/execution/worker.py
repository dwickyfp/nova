"""Small, credential-protected job contract for the ML process boundary."""

from __future__ import annotations

import asyncio
import resource
import sys
import time
from dataclasses import dataclass, replace

from app.core.security import decrypt_password
from app.modules.ml_engine.data.datasource import ExtractionMetrics, collect_bounded
from app.modules.ml_engine.data.mysql_fallback import PreferredDataSource
from app.modules.ml_engine.engines.anomaly import train_anomaly
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.engines.clustering import train_clustering
from app.modules.ml_engine.engines.forecast import train_forecast
from app.modules.ml_engine.engines.tabular import train_tabular
from app.modules.ml_engine.spec import MLExecutionSpec, MLSecurityContext, MLTask


@dataclass(frozen=True)
class MLWorkerSecurity:
    """The caller identity sent to a child without a plaintext password."""

    username: str
    encrypted_password: str
    database: str | None
    schema: str | None
    role: str | None
    tenant: str


@dataclass(frozen=True)
class MLWorkerJob:
    run_id: str
    spec: MLExecutionSpec
    normalized_input_sql: str
    security: MLWorkerSecurity
    dispatched_at: float = 0.0


@dataclass(frozen=True)
class MLWorkerResult:
    output: TrainingOutput
    extraction: ExtractionMetrics
    worker_startup_seconds: float = 0.0
    training_seconds: float = 0.0
    worker_peak_rss_bytes: int = 0


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
    )
    if job.spec.budget is None:  # fail closed at the process boundary
        raise ValueError("ML worker job is missing an execution budget")
    dataset = asyncio.run(
        collect_bounded(
            PreferredDataSource(),
            job.normalized_input_sql,
            security,
            job.spec.budget,
        )
    )
    worker_spec = replace(job.spec, security=security)
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
    return MLWorkerResult(
        output=output,
        extraction=dataset.metrics,
        worker_startup_seconds=(
            max(0.0, worker_started - job.dispatched_at) if job.dispatched_at else 0.0
        ),
        training_seconds=time.perf_counter() - training_started,
        worker_peak_rss_bytes=_peak_rss_bytes(),
    )


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)
