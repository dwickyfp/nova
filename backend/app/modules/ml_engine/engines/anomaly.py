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
        normalized = _rank_percentiles(scores)
        evaluated.append((name, model, scores, normalized))
    consensus = np.mean([item[3] for item in evaluated], axis=0)
    agreed = [
        (
            float(np.nan_to_num(np.corrcoef(normalized, consensus)[0, 1], nan=-1.0)),
            name,
            model,
            normalized,
        )
        for name, model, _, normalized in evaluated
    ]
    agreement, algorithm, model, scores = max(agreed, key=lambda item: item[0])
    cutoff = float(np.quantile(scores, 1.0 - contamination))
    labels = (scores >= cutoff).astype(int)
    identifiers = (
        table.column(spec.row_identifier)
        if spec.row_identifier and spec.row_identifier in table.column_names
        else pa.array(np.arange(table.num_rows))
    )
    result_table = pa.table(
        {
            "row_id": identifiers,
            "anomaly_score": pa.array(scores),
            "is_anomaly": pa.array(labels.astype(bool)),
        }
    )
    metrics = {
        "selected_estimator": algorithm,
        "contamination": contamination,
        "anomalies": int(labels.sum()),
        "selection_method": "rank_normalized_consensus_agreement",
        "score_orientation": "higher_is_more_anomalous_percentile",
        "candidate_agreement": {name: score for score, name, _, _ in agreed},
        "selected_agreement": agreement,
        "feature_metadata": serialize_profiles(preprocessor.profiles),
        "arrow_to_pandas_seconds": preprocessor.arrow_to_pandas_seconds,
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
        results=result_table.slice(0, 1000).to_pylist(),
        result_table=result_table,
    )


def _rank_percentiles(values: np.ndarray) -> np.ndarray:
    """Normalize detector-specific score scales into stable [0, 1] ranks."""
    order = np.argsort(np.argsort(values, kind="mergesort"), kind="mergesort")
    denominator = max(1, len(values) - 1)
    return order.astype(float) / denominator
