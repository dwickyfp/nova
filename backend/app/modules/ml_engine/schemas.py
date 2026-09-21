"""ML Engine schemas — Pydantic models for training, prediction, and model management."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Training ──────────────────────────────────────────────────


class TrainModelRequest(BaseModel):
    """Train a classical ML model using data from a SQL query."""

    model_name: str = Field(
        ..., min_length=1, max_length=256, description="Human-readable model name"
    )
    model_type: str = Field(
        ...,
        pattern="^(classification|regression|forecast|anomaly_detection|clustering)$",
        description=(
            "Type of ML problem: classification, regression, forecast, or "
            "anomaly_detection, or clustering"
        ),
    )
    algorithm: str = Field(
        default="auto",
        pattern="^[A-Za-z0-9_()=.-]+$",
        description="Algorithm to use. 'auto' performs budget-aware candidate evaluation.",
    )
    training_sql: str = Field(..., min_length=10, description="SQL query to fetch training data")
    target_column: str | None = Field(default=None, description="Column name to predict")
    feature_columns: list[str] | None = Field(
        default=None,
        description="Feature columns to use. If None, uses all columns except target.",
    )
    hyperparameters: dict[str, Any] | None = Field(
        default=None,
        description="Algorithm-specific hyperparameters (e.g., {'n_estimators': 100})",
    )
    test_size: float = Field(
        default=0.2, ge=0.0, lt=1.0, description="Fraction of data for testing"
    )
    database_name: str | None = Field(
        default=None, description="Database context for the training SQL"
    )
    timestamp_column: str | None = None
    series_column: str | None = None
    horizon: int | None = Field(default=None, ge=1)
    frequency: str | None = None
    mode: Literal["interactive", "balanced", "best"] = "balanced"


class MLExecuteRequest(BaseModel):
    """Run persistent or on-the-fly ML through the common execution contract."""

    task: Literal["classification", "regression", "forecast", "anomaly_detection", "clustering"]
    input_sql: str = Field(..., min_length=6)
    feature_columns: list[str] | None = None
    target_column: str | None = None
    timestamp_column: str | None = None
    series_column: str | None = None
    row_identifier: str | None = None
    horizon: int | None = Field(default=None, ge=1)
    frequency: str | None = None
    mode: Literal["interactive", "balanced", "best"] = "interactive"
    persist: bool = False
    model_name: str | None = Field(default=None, max_length=256)
    algorithm: str = "auto"
    metric: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    database_name: str | None = None
    schema_name: str | None = None


class MLExecuteResponse(BaseModel):
    run_id: str
    task: str
    mode: str
    status: str
    selected_engine: str
    selected_algorithm: str
    training_rows: int
    feature_columns: list[str]
    metrics: dict[str, Any]
    results: list[dict[str, Any]]
    model_id: str | None = None
    version: int | None = None
    cache_hit: bool = False
    telemetry: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


class PromoteRunRequest(BaseModel):
    model_name: str = Field(..., min_length=1, max_length=256)
    database_name: str | None = None
    schema_name: str | None = None


class TrainModelResponse(BaseModel):
    """Response after model training."""

    model_id: str
    model_name: str
    model_type: str
    algorithm: str
    version: int
    status: str
    training_rows: int
    feature_columns: list[str]
    metrics: dict[str, Any]
    message: str | None = None


# ── Prediction ────────────────────────────────────────────────


class PredictRequest(BaseModel):
    """Run prediction using a trained model."""

    model_alias: str = Field(..., min_length=1, description="Model alias name")
    features: dict[str, Any] = Field(
        ...,
        description="Feature values as column_name -> value",
    )
    database_name: str | None = None


class PredictResponse(BaseModel):
    """Prediction result."""

    model_alias: str
    model_name: str
    prediction: Any
    probability: dict[str, float] | None = None
    model_version: int


class VersionPredictRequest(BaseModel):
    """Run an immutable historical model version."""

    model_id: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    features: dict[str, Any]
    database_name: str | None = None


class VersionPredictResponse(BaseModel):
    model_id: str
    model_name: str
    prediction: Any
    probability: dict[str, float] | None = None
    model_version: int


class BatchPredictRequest(BaseModel):
    """Batch prediction using a SQL query to fetch features."""

    model_alias: str = Field(..., min_length=1)
    prediction_sql: str = Field(
        ...,
        min_length=10,
        description="SQL query that returns feature columns for prediction",
    )
    database_name: str | None = Field(default=None)


class BatchPredictResponse(BaseModel):
    """Batch prediction results."""

    model_alias: str
    model_name: str
    predictions: list[dict[str, Any]]
    total_rows: int


# ── Model Management ──────────────────────────────────────────


class ModelInfo(BaseModel):
    """Model metadata."""

    model_id: str
    model_name: str
    model_type: str
    algorithm: str | None = None
    target_column: str | None = None
    feature_columns: list[str] | None = None
    hyperparameters: dict[str, Any] | None = None
    training_sql: str | None = None
    database_name: str | None = None
    created_at: str | None = None
    created_by: str | None = None
    # Latest version info
    latest_version: int | None = None
    latest_status: str | None = None
    latest_metrics: dict[str, Any] | None = None
    training_rows: int | None = None


class ModelListResponse(BaseModel):
    models: list[ModelInfo]
    count: int


class ModelDetailResponse(BaseModel):
    model: ModelInfo
    versions: list[dict[str, Any]]


class ModelAliasCreate(BaseModel):
    """Create or update a model alias."""

    alias_name: str = Field(..., min_length=1, max_length=128)
    model_id: str
    version: int = Field(..., ge=1)
    database_name: str | None = None


class ModelAliasResponse(BaseModel):
    alias_name: str
    model_id: str
    model_name: str | None = None
    version: int
    created_at: str | None = None


class ModelAliasListResponse(BaseModel):
    aliases: list[ModelAliasResponse]
    count: int


class DeleteModelResponse(BaseModel):
    model_id: str
    deleted: bool
    message: str | None = None
