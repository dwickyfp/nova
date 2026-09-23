"""Stable task and budget contract for every Nova ML entry point."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.core.config import settings


class MLTask(StrEnum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    FORECAST = "forecast"
    ANOMALY_DETECTION = "anomaly_detection"
    CLUSTERING = "clustering"


class MLMode(StrEnum):
    INTERACTIVE = "interactive"
    BALANCED = "balanced"
    BEST = "best"


@dataclass(frozen=True)
class ExecutionBudget:
    timeout_seconds: float
    max_rows: int
    max_bytes: int
    max_concurrency: int = 1


@dataclass(frozen=True)
class MLSecurityContext:
    username: str
    password: str
    database: str | None = None
    schema: str | None = None
    role: str | None = None
    security_context_version: int = 1
    tenant: str = "default"

    def validate(self) -> None:
        if not settings.RANGER_ENABLED:
            return
        if not self.username.strip() or self.username.casefold() == "root":
            raise InvalidMLSpec("ML data access requires a non-root principal")
        if not self.role or "," in self.role or self.role.upper() in {"ALL", "NONE", "DEFAULT"}:
            raise InvalidMLSpec("ML data access requires exactly one active role")
        if self.security_context_version < 1:
            raise InvalidMLSpec("ML security context version must be positive")

    @property
    def scope_key(self) -> str:
        value = (
            self.tenant,
            self.username,
            self.role or "",
            str(self.security_context_version),
            self.database or "",
            self.schema or "",
        )
        return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class MLExecutionSpec:
    task: MLTask
    input_sql: str
    security: MLSecurityContext
    mode: MLMode = MLMode.INTERACTIVE
    persist: bool = False
    model_name: str | None = None
    algorithm: str = "auto"
    feature_columns: tuple[str, ...] = ()
    target_column: str | None = None
    timestamp_column: str | None = None
    series_column: str | None = None
    row_identifier: str | None = None
    horizon: int | None = None
    frequency: str | None = None
    metric: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    budget: ExecutionBudget | None = None
    deadline_at: float | None = None

    def validate(self) -> None:
        self.security.validate()
        if not self.input_sql.strip():
            raise InvalidMLSpec("input_sql is required")
        if self.persist and not self.model_name:
            raise InvalidMLSpec("model_name is required when persist=true")
        if self.task in {MLTask.CLASSIFICATION, MLTask.REGRESSION} and not self.target_column:
            raise InvalidMLSpec(f"target_column is required for {self.task.value}")
        if self.task is MLTask.FORECAST:
            if not self.target_column:
                raise InvalidMLSpec("target_column is required for forecast")
            if not self.timestamp_column:
                raise InvalidMLSpec("timestamp_column is required for forecast")
            if not self.horizon or self.horizon < 1:
                raise InvalidMLSpec("horizon must be at least 1 for forecast")


@dataclass
class MLRunResult:
    run_id: str
    task: str
    mode: str
    status: str
    selected_engine: str
    selected_algorithm: str
    training_rows: int
    feature_columns: list[str]
    metrics: dict[str, Any]
    results: list[dict[str, Any]] = field(default_factory=list)
    model_id: str | None = None
    version: int | None = None
    artifact_uri: str | None = None
    cache_hit: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


class MLError(ValueError):
    code = "ml_error"


class InvalidMLSpec(MLError):
    code = "invalid_ml_specification"


class InsufficientTrainingRows(MLError):
    code = "insufficient_training_rows"


class UnsupportedFeatureType(MLError):
    code = "unsupported_feature_type"


class DataBudgetExceeded(MLError):
    code = "data_budget_exceeded"


class TrainingTimeout(MLError):
    code = "training_timeout"


class MLExecutionTimeout(TrainingTimeout):
    code = "ml_execution_timeout"

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.code = f"ml_{stage}_timeout"
        super().__init__(f"ML execution deadline expired during {stage}")

    def __reduce__(self):
        return type(self), (self.stage,)


class UnsupportedMLSQLExpression(MLError):
    code = "ml_unsupported_sql_expression"


class MLMemoryBudgetExceeded(MLError):
    code = "ml_memory_budget_exceeded"


class CorruptArtifact(MLError):
    code = "corrupt_artifact"


class InferenceSchemaMismatch(MLError):
    code = "inference_schema_mismatch"


class VersionReservationConflict(MLError):
    code = "version_reservation_conflict"
