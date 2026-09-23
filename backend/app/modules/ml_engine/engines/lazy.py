"""Load training engines only in the process that executes a training job."""

from __future__ import annotations

import pyarrow as pa

from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.spec import MLExecutionSpec


def train_tabular(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    from app.modules.ml_engine.engines.tabular import train_tabular as run

    return run(table, spec)


def train_forecast(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    from app.modules.ml_engine.engines.forecast import train_forecast as run

    return run(table, spec)


def train_anomaly(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    from app.modules.ml_engine.engines.anomaly import train_anomaly as run

    return run(table, spec)


def train_clustering(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    from app.modules.ml_engine.engines.clustering import train_clustering as run

    return run(table, spec)
