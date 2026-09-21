"""Unsupervised anomaly detection with PyOD candidates."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
from pyod.models.copod import COPOD
from pyod.models.ecod import ECOD
from pyod.models.hbos import HBOS
from pyod.models.iforest import IForest

from app.core.config import settings
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.preprocessing.preprocessor import FeaturePreprocessor
from app.modules.ml_engine.preprocessing.profiler import serialize_profiles
from app.modules.ml_engine.spec import InsufficientTrainingRows, MLExecutionSpec


def train_anomaly(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    features = list(spec.feature_columns) or [
        name for name in table.column_names if name != spec.row_identifier
    ]
    if table.num_rows < 10:
        raise InsufficientTrainingRows("Anomaly detection requires at least 10 rows")
    preprocessor = FeaturePreprocessor(features, scale_numeric=True)
    X = preprocessor.fit_transform(table)
    if hasattr(X, "toarray"):
        X = X.toarray()
    contamination = float(spec.parameters.get("contamination", 0.05))
    if not 0 < contamination < 0.5:
        raise ValueError("contamination must be between 0 and 0.5")
    candidates = [
        ("ecod", ECOD(contamination=contamination)),
        ("copod", COPOD(contamination=contamination)),
        ("iforest", IForest(contamination=contamination, random_state=settings.ML_RANDOM_SEED)),
    ]
    if spec.mode.value != "interactive":
        candidates.append(("hbos", HBOS(contamination=contamination)))
    if spec.algorithm != "auto":
        candidates = [item for item in candidates if item[0] == spec.algorithm]
        if not candidates:
            raise ValueError(f"Unsupported anomaly algorithm: {spec.algorithm}")
    evaluated = []
    for name, model in candidates:
        model.fit(X)
        scores = np.asarray(model.decision_scores_, dtype=float)
        separation = float(np.quantile(scores, 0.95) - np.median(scores))
        evaluated.append((separation, name, model, scores))
    _, algorithm, model, scores = max(evaluated, key=lambda item: item[0])
    labels = np.asarray(model.labels_, dtype=int)
    results = []
    identifiers = (
        table.column(spec.row_identifier).to_pylist()
        if spec.row_identifier and spec.row_identifier in table.column_names
        else list(range(table.num_rows))
    )
    for identifier, score, label in zip(identifiers, scores, labels, strict=False):
        results.append(
            {"row_id": identifier, "anomaly_score": float(score), "is_anomaly": bool(label)}
        )
    metrics = {
        "selected_estimator": algorithm,
        "contamination": contamination,
        "anomalies": int(labels.sum()),
        "candidate_scores": {name: score for score, name, _, _ in evaluated},
        "feature_metadata": serialize_profiles(preprocessor.profiles),
    }
    return TrainingOutput(
        bundle={
            "task": "anomaly_detection",
            "model": model,
            "preprocessor": preprocessor,
            "feature_columns": features,
        },
        engine="pyod",
        algorithm=algorithm,
        metrics=metrics,
        feature_columns=features,
        training_rows=table.num_rows,
        results=results,
    )
