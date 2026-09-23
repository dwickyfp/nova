"""Budget-aware clustering with bounded candidate and K search."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
from sklearn.cluster import Birch, KMeans, MiniBatchKMeans
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture

from app.core.config import settings
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.preprocessing.memory import bounded_dense
from app.modules.ml_engine.preprocessing.preprocessor import FeaturePreprocessor
from app.modules.ml_engine.preprocessing.profiler import serialize_profiles
from app.modules.ml_engine.spec import InsufficientTrainingRows, MLExecutionSpec


def train_clustering(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    features = list(spec.feature_columns) or [
        name for name in table.column_names if name != spec.row_identifier
    ]
    if table.num_rows < 6:
        raise InsufficientTrainingRows("Clustering requires at least 6 rows")
    preprocessor = FeaturePreprocessor(features, scale_numeric=True)
    X = preprocessor.fit_transform(table)
    X = bounded_dense(X, max_bytes=spec.budget.max_bytes if spec.budget else None)
    max_k = min(int(spec.parameters.get("max_clusters", 8)), max(2, int(np.sqrt(len(X)))))
    candidates = []
    for k in range(2, max_k + 1):
        if len(X) > 20_000:
            candidates.append(
                (
                    f"minibatch_kmeans(k={k})",
                    MiniBatchKMeans(n_clusters=k, random_state=settings.ML_RANDOM_SEED, n_init=3),
                )
            )
        else:
            candidates.append(
                (
                    f"kmeans(k={k})",
                    KMeans(n_clusters=k, random_state=settings.ML_RANDOM_SEED, n_init=10),
                )
            )
        if spec.mode.value == "best":
            candidates.append((f"birch(k={k})", Birch(n_clusters=k)))
            if len(X) <= 20_000:
                candidates.append(
                    (
                        f"gaussian_mixture(k={k})",
                        GaussianMixture(
                            n_components=k,
                            covariance_type="full",
                            random_state=settings.ML_RANDOM_SEED,
                        ),
                    )
                )
    evaluated = []
    sample_limit = min(len(X), 10_000)
    for name, model in candidates:
        labels = model.fit_predict(X) if hasattr(model, "fit_predict") else model.fit(X).predict(X)
        unique = np.unique(labels[labels >= 0])
        if len(unique) < 2:
            continue
        silhouette = float(
            silhouette_score(
                X, labels, sample_size=sample_limit, random_state=settings.ML_RANDOM_SEED
            )
        )
        db = float(davies_bouldin_score(X, labels))
        ch = float(calinski_harabasz_score(X, labels))
        evaluated.append((silhouette, name, model, labels, db, ch))
    if not evaluated:
        raise ValueError("Clustering produced only a single cluster or noise")
    silhouette, algorithm, model, labels, db, ch = max(evaluated, key=lambda item: item[0])
    identifiers = (
        table.column(spec.row_identifier)
        if spec.row_identifier and spec.row_identifier in table.column_names
        else pa.array(np.arange(table.num_rows))
    )
    result_table = pa.table(
        {"row_id": identifiers, "cluster_id": pa.array(labels.astype(np.int64))}
    )
    metrics = {
        "selected_estimator": algorithm,
        "silhouette": silhouette,
        "davies_bouldin": db,
        "calinski_harabasz": ch,
        "cluster_count": int(len(np.unique(labels[labels >= 0]))),
        "candidates_evaluated": len(evaluated),
        "feature_metadata": serialize_profiles(preprocessor.profiles),
        "preprocessing_policy": {
            "categorical_encoding": "bounded_one_hot",
            "max_categories": preprocessor.max_categories,
        },
        "arrow_to_pandas_seconds": preprocessor.arrow_to_pandas_seconds,
    }
    return TrainingOutput(
        bundle={
            "task": "clustering",
            "model": model,
            "preprocessor": preprocessor,
            "feature_columns": features,
        },
        engine="scikit_learn_clustering",
        algorithm=algorithm,
        metrics=metrics,
        feature_columns=features,
        training_rows=table.num_rows,
        results=result_table.slice(0, 1000).to_pylist(),
        result_table=result_table,
    )
