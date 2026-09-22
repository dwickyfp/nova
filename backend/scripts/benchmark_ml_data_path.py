"""Repeatable ML extraction/preprocessing smoke benchmark; no timing assertions."""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time

import numpy as np
import pyarrow as pa
from sklearn.linear_model import LogisticRegression

from app.core.config import settings
from app.core.security import encrypt_password
from app.modules.ml_engine.artifacts.serializer import deserialize_bundle, serialize_bundle
from app.modules.ml_engine.execution.budgets import budget_for
from app.modules.ml_engine.execution.job_runner import MLJobRunner
from app.modules.ml_engine.execution.worker import MLWorkerJob, MLWorkerSecurity
from app.modules.ml_engine.preprocessing.preprocessor import FeaturePreprocessor
from app.modules.ml_engine.runtime.model_cache import ModelCache
from app.modules.ml_engine.spec import MLExecutionSpec, MLMode, MLSecurityContext, MLTask


def run(rows: int, batch_size: int) -> dict:
    rng = np.random.default_rng(42)
    started = time.perf_counter()
    batches = []
    for offset in range(0, rows, batch_size):
        count = min(batch_size, rows - offset)
        batches.append(
            pa.record_batch(
                [
                    pa.array(rng.normal(size=count)),
                    pa.array(rng.integers(0, 5, size=count).astype(str)),
                ],
                names=["value", "category"],
            )
        )
    table = pa.Table.from_batches(batches)
    extraction_seconds = time.perf_counter() - started
    legacy_started = time.perf_counter()
    legacy_rows = table.to_pylist()
    legacy_seconds = time.perf_counter() - legacy_started
    preprocessing_started = time.perf_counter()
    preprocessor = FeaturePreprocessor(["value", "category"])
    matrix = preprocessor.fit_transform(table)
    preprocessing_seconds = time.perf_counter() - preprocessing_started
    target = (table.column("value").to_numpy() > 0).astype(int)
    training_started = time.perf_counter()
    model = LogisticRegression(max_iter=200, random_state=42).fit(matrix, target)
    training_seconds = time.perf_counter() - training_started
    prediction_started = time.perf_counter()
    model.predict(matrix)
    prediction_seconds = time.perf_counter() - prediction_started
    payload, checksum = serialize_bundle(
        {
            "task": "classification",
            "model": model,
            "preprocessor": preprocessor,
            "feature_columns": ["value", "category"],
        }
    )
    cache_metrics = asyncio.run(_cache_smoke(payload, checksum))
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = int(peak_rss if sys.platform == "darwin" else peak_rss * 1024)
    return {
        "rows": rows,
        "bytes": table.nbytes,
        "batches": len(batches),
        "extraction_rows_per_second": round(rows / extraction_seconds, 2),
        "extraction_mb_per_second": round(table.nbytes / extraction_seconds / 1024 / 1024, 2),
        "legacy_object_rows_per_second": round(rows / legacy_seconds, 2),
        "legacy_python_objects": len(legacy_rows),
        "preprocessing_rows_per_second": round(rows / preprocessing_seconds, 2),
        "training_duration_seconds": round(training_seconds, 6),
        "prediction_rows_per_second": round(rows / prediction_seconds, 2),
        "artifact_bytes": len(payload),
        "model_cache": cache_metrics,
        "matrix_shape": list(matrix.shape),
        "peak_rss_bytes": peak_rss_bytes,
    }


async def _cache_smoke(payload: bytes, checksum: str) -> dict:
    cache = ModelCache(max_models=2, max_bytes=len(payload) * 2, ttl_seconds=60)
    loads = 0

    async def loader():
        nonlocal loads
        loads += 1
        return deserialize_bundle(payload, checksum), len(payload)

    await cache.get_or_load(("benchmark", 1), loader)
    await cache.get_or_load(("benchmark", 1), loader)
    return {**cache.metrics(), "deserializations": loads}


async def run_worker_direct(sql: str, database: str, target: str) -> dict:
    """Measure the real descriptor -> spawn -> Flight -> engine path."""
    runner = MLJobRunner(max_workers=1)
    budget = budget_for(MLMode.BEST)
    security = MLSecurityContext(
        settings.STARROCKS_ROOT_USER,
        "",
        database=database,
    )
    job = MLWorkerJob(
        run_id="benchmark",
        spec=MLExecutionSpec(
            task=MLTask.REGRESSION,
            input_sql="[worker-direct benchmark]",
            security=MLSecurityContext(security.username, "", database=database),
            target_column=target,
            algorithm="ridge",
            mode=MLMode.BEST,
            budget=budget,
        ),
        normalized_input_sql=sql,
        security=MLWorkerSecurity(
            username=security.username,
            encrypted_password=encrypt_password(settings.STARROCKS_ROOT_PASSWORD),
            database=database,
            schema=None,
            role=None,
            tenant="default",
        ),
        dispatched_at=time.perf_counter(),
    )
    started = time.perf_counter()
    api_rss_before = _peak_rss_bytes()
    try:
        result = await runner.run_job(job, timeout_seconds=budget.timeout_seconds)
    finally:
        runner.close()
    wall = time.perf_counter() - started
    api_peak_rss_bytes = _peak_rss_bytes()
    extraction = result.extraction
    return {
        "architecture": "api_descriptor_to_worker_direct_flight",
        "wall_seconds": round(wall, 6),
        "rows": extraction.rows_read,
        "bytes": extraction.bytes_read,
        "record_batches": extraction.batches_read,
        "queue_wait_seconds": extraction.queue_wait_seconds,
        "data_conversion_duration": extraction.data_conversion_duration,
        "largest_batch_bytes": extraction.largest_batch_bytes,
        "final_materialized_bytes": extraction.final_materialized_bytes,
        "ipc_bytes": 0,
        "worker_startup_seconds": result.worker_startup_seconds,
        "training_seconds": result.training_seconds,
        "api_peak_rss_bytes": api_peak_rss_bytes,
        "api_peak_rss_growth_bytes": max(0, api_peak_rss_bytes - api_rss_before),
        "worker_peak_rss_bytes": result.worker_peak_rss_bytes,
        "rows_per_second": round(extraction.rows_read / wall, 2),
        "mb_per_second": round(extraction.bytes_read / wall / 1024 / 1024, 2),
        "engine": result.output.engine,
        "algorithm": result.output.algorithm,
    }


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=65_536)
    parser.add_argument("--worker-direct-sql")
    parser.add_argument("--database", default="NOVA_EXAMPLE")
    parser.add_argument("--target", default="target")
    args = parser.parse_args()
    output = {"synthetic_columnar": run(args.rows, args.batch_size)}
    if args.worker_direct_sql:
        output["worker_direct"] = asyncio.run(
            run_worker_direct(args.worker_direct_sql, args.database, args.target)
        )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
