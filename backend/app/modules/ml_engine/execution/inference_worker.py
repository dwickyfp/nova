"""Stream prediction batches directly from the engine into result storage."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import time
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.core.security import decrypt_password
from app.modules.ml_engine.artifacts.serializer import deserialize_bundle
from app.modules.ml_engine.artifacts.store import ObjectArtifactStore
from app.modules.ml_engine.data.batches import inference_batches
from app.modules.ml_engine.data.mysql_fallback import PreferredDataSource
from app.modules.ml_engine.execution.deadline import ExecutionDeadline
from app.modules.ml_engine.execution.worker import MLWorkerSecurity, _peak_rss_bytes
from app.modules.ml_engine.runtime.model_runtime import ModelRuntime
from app.modules.ml_engine.spec import DataBudgetExceeded, MLExecutionTimeout, MLSecurityContext


@dataclass(frozen=True)
class MLInferenceJob:
    run_id: str
    encrypted_sql: str
    security: MLWorkerSecurity
    model_metadata: dict[str, Any]
    result_prefix: str
    deadline_at: float
    max_rows: int
    max_bytes: int


def execute_inference_job(job: MLInferenceJob) -> dict[str, Any]:
    store = ObjectArtifactStore()
    metadata = job.model_metadata
    bundle = deserialize_bundle(store.get(metadata["artifact_uri"]), metadata["artifact_sha256"])
    security = MLSecurityContext(
        username=job.security.username,
        password=decrypt_password(job.security.encrypted_password),
        database=job.security.database,
        schema=job.security.schema,
        role=job.security.role,
        tenant=job.security.tenant,
        security_context_version=job.security.security_context_version,
    )
    security.validate()
    return asyncio.run(
        materialize_batches(
            job,
            bundle,
            PreferredDataSource(),
            store,
            security,
            decrypt_password(job.encrypted_sql),
        )
    )


async def materialize_batches(job, bundle, source, store, security, sql) -> dict[str, Any]:
    deadline = ExecutionDeadline(job.deadline_at)
    started = time.monotonic()
    parts: list[str] = []
    rows = bytes_read = result_bytes = peak = 0
    stream = source.stream(sql, security)
    try:
        async with asyncio.timeout(deadline.remaining("inference")):
            async for incoming in stream:
                rows += incoming.num_rows
                bytes_read += incoming.nbytes
                if rows > job.max_rows or bytes_read > job.max_bytes:
                    raise DataBudgetExceeded("Materialized inference exceeds its input budget")
                for batch in inference_batches(incoming):
                    deadline.remaining("inference")
                    peak = max(peak, batch.nbytes)
                    prediction = ModelRuntime.predict_bundle_table(
                        bundle, pa.Table.from_batches([batch])
                    )
                    for output_batch in inference_batches(prediction):
                        buffer = io.BytesIO()
                        pq.write_table(pa.Table.from_batches([output_batch]), buffer)
                        payload = buffer.getvalue()
                        result_bytes += len(payload)
                        uri = store.put(
                            f"{job.result_prefix}/part-{len(parts):08d}.parquet", payload
                        )
                        parts.append(uri)
                    deadline.remaining("inference")
        manifest = json.dumps({"parts": parts, "rows": rows}).encode()
        uri = store.put(f"{job.result_prefix}/manifest.json", manifest)
        return {
            "run_id": job.run_id,
            "result_uri": uri,
            "result_rows": rows,
            "result_bytes": result_bytes,
            "result_parts": len(parts),
            "rows_read": rows,
            "bytes_read": bytes_read,
            "largest_batch_bytes": peak,
            "worker_peak_rss_bytes": _peak_rss_bytes(),
            "inference_ms": (time.monotonic() - started) * 1000,
            "transport_used": getattr(source, "transport_used", type(source).__name__),
            "dataset_ipc_bytes": 0,
            "trained_model_ipc_bytes": 0,
            "result_destination": "object_storage",
        }
    except BaseException as exc:
        for uri in parts:
            with contextlib.suppress(Exception):
                store.delete(uri)
        if isinstance(exc, TimeoutError):
            raise MLExecutionTimeout("inference") from exc
        raise
    finally:
        await stream.aclose()
