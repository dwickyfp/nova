"""Classification and regression engine with constrained candidate evaluation."""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

from app.core.config import settings
from app.modules.ml_engine.automl.router import AutoMLRouter, evaluate_predictions
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.preprocessing.preprocessor import FeaturePreprocessor
from app.modules.ml_engine.preprocessing.profiler import serialize_profiles
from app.modules.ml_engine.spec import InsufficientTrainingRows, MLExecutionSpec, MLTask


def train_tabular(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    if spec.target_column not in table.column_names:
        raise ValueError(f"Target column '{spec.target_column}' is not present")
    features = list(spec.feature_columns) or [
        name for name in table.column_names if name != spec.target_column
    ]
    target = table.column(spec.target_column).to_pandas()
    valid = ~target.isna()
    filtered = table.filter(pa.array(valid.to_numpy()))
    target = target[valid].reset_index(drop=True)
    if filtered.num_rows < 10:
        raise InsufficientTrainingRows("At least 10 rows with a non-null target are required")
    preprocessor = FeaturePreprocessor(features)
    X = preprocessor.fit_transform(filtered)
    label_encoder = None
    y = target.to_numpy()
    if spec.task is MLTask.CLASSIFICATION and (
        target.dtype == object or str(target.dtype).startswith("string")
    ):
        label_encoder = LabelEncoder()
        y = label_encoder.fit_transform(y)
    stratify = y if spec.task is MLTask.CLASSIFICATION and len(np.unique(y)) > 1 else None
    test_size = float(spec.parameters.get("test_size", 0.2))
    # Legacy callers used ``0`` to mean "choose a safe default". AutoML still
    # requires a validation holdout, so retain that API behavior without
    # silently scoring on the training rows.
    if test_size <= 0:
        test_size = 0.2
    X_train, X_valid, y_train, y_valid = train_test_split(
        X,
        y,
        test_size=test_size,
        random_state=settings.ML_RANDOM_SEED,
        stratify=stratify,
    )
    budget = spec.budget
    assert budget is not None
    estimator_parameters = spec.parameters.get("estimator_parameters")
    if estimator_parameters is None:
        # Compatibility for early MLExecutionSpec callers. Runtime controls are
        # removed; every remaining key is treated as an estimator parameter.
        estimator_parameters = {
            key: value
            for key, value in spec.parameters.items()
            if key not in {"test_size", "data_freshness_token"}
        }
    if not isinstance(estimator_parameters, dict):
        raise ValueError("estimator_parameters must be an object")
    if spec.algorithm == "auto" and estimator_parameters:
        raise ValueError(
            "Estimator hyperparameters require an explicit ALGORITHM; auto search "
            "does not silently override or ignore them"
        )
    selection = AutoMLRouter(random_seed=settings.ML_RANDOM_SEED).select(
        task=spec.task,
        mode=spec.mode,
        X_train=X_train,
        y_train=y_train,
        X_valid=X_valid,
        y_valid=y_valid,
        timeout_seconds=budget.timeout_seconds,
        algorithm=spec.algorithm,
        metric=spec.metric,
        parameters=estimator_parameters,
    )
    predicted = selection.estimator.predict(X_valid)
    metrics: dict[str, Any] = evaluate_predictions(spec.task, y_valid, predicted)
    metric_name = selection.metric
    metric_value = metrics.get(metric_name)
    metrics.update(
        {
            "selected_estimator": selection.name,
            "selection_metric": selection.metric,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "higher_is_better": True,
            "loss_name": "flaml_validation_loss" if selection.backend == "flaml" else None,
            "loss_value": selection.loss_value,
            # Compatibility field, now always a directly computed metric rather
            # than the invalid universal ``1 - loss`` transformation.
            "validation_score": metric_value,
            "candidates_evaluated": selection.candidates_evaluated,
            "search_duration_seconds": round(selection.duration_seconds, 6),
            "hyperparameters": selection.hyperparameters,
            "feature_metadata": serialize_profiles(preprocessor.profiles),
            "arrow_to_pandas_seconds": preprocessor.arrow_to_pandas_seconds,
        }
    )
    bundle = {
        "task": spec.task.value,
        "model": selection.estimator,
        "preprocessor": preprocessor,
        "feature_columns": features,
        "target_column": spec.target_column,
        "label_encoder": label_encoder,
    }
    return TrainingOutput(
        bundle=bundle,
        engine=selection.backend,
        algorithm=selection.name,
        metrics=metrics,
        feature_columns=features,
        training_rows=filtered.num_rows,
    )
