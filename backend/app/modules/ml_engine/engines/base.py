"""Contracts shared by deterministic ML engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pyarrow as pa

from app.modules.ml_engine.spec import MLExecutionSpec


@dataclass
class TrainingOutput:
    bundle: dict[str, Any]
    engine: str
    algorithm: str
    metrics: dict[str, Any]
    feature_columns: list[str]
    training_rows: int
    results: list[dict[str, Any]] = field(default_factory=list)


class MLEngine(Protocol):
    def train(self, table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput: ...
