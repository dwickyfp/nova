"""Columnar inference chunks bounded by both row count and logical bytes."""

from collections.abc import Iterator

import pyarrow as pa

from app.core.config import settings
from app.modules.ml_engine.spec import DataBudgetExceeded


def inference_batches(table: pa.Table | pa.RecordBatch) -> Iterator[pa.RecordBatch]:
    rows = settings.ML_INFERENCE_BATCH_ROWS
    limit = settings.ML_INFERENCE_MAX_BATCH_BYTES
    if rows < 1 or limit < 1:
        raise ValueError("ML inference batch limits must be positive")
    source = table if isinstance(table, pa.Table) else pa.Table.from_batches([table])
    for batch in source.to_batches(max_chunksize=rows):
        yield from _split_bytes(batch, limit)


def _split_bytes(batch: pa.RecordBatch, limit: int) -> Iterator[pa.RecordBatch]:
    if batch.nbytes <= limit:
        yield batch
    elif batch.num_rows <= 1:
        raise DataBudgetExceeded("One inference row exceeds the configured batch byte limit")
    else:
        middle = batch.num_rows // 2
        yield from _split_bytes(batch.slice(0, middle), limit)
        yield from _split_bytes(batch.slice(middle), limit)
