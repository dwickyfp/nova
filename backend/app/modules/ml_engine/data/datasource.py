"""Columnar training-data contracts and bounded collection."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import pyarrow as pa

from app.modules.ml_engine.spec import DataBudgetExceeded, ExecutionBudget, MLSecurityContext


@dataclass
class ExtractionMetrics:
    rows_read: int = 0
    bytes_read: int = 0
    batches_read: int = 0
    duration_ms: float = 0.0
    transport: str = "unknown"


@dataclass
class ColumnarDataset:
    table: pa.Table
    metrics: ExtractionMetrics


class TrainingDataSource(Protocol):
    async def stream(
        self, sql: str, security: MLSecurityContext
    ) -> AsyncIterator[pa.RecordBatch]: ...


async def collect_bounded(
    source: TrainingDataSource,
    sql: str,
    security: MLSecurityContext,
    budget: ExecutionBudget,
) -> ColumnarDataset:
    started = time.perf_counter()
    batches: list[pa.RecordBatch] = []
    metrics = ExtractionMetrics(transport=type(source).__name__)
    async for batch in source.stream(sql, security):
        metrics.rows_read += batch.num_rows
        metrics.bytes_read += batch.nbytes
        metrics.batches_read += 1
        if metrics.rows_read > budget.max_rows:
            raise DataBudgetExceeded(
                f"ML input exceeds the {budget.max_rows:,}-row budget; narrow the query "
                "or select a larger execution mode"
            )
        if metrics.bytes_read > budget.max_bytes:
            raise DataBudgetExceeded(
                f"ML input exceeds the {budget.max_bytes:,}-byte budget; narrow the query "
                "or select a larger execution mode"
            )
        batches.append(batch)
    if not batches:
        raise ValueError("ML input query returned no rows")
    metrics.duration_ms = round((time.perf_counter() - started) * 1000, 3)
    return ColumnarDataset(pa.Table.from_batches(batches), metrics)
