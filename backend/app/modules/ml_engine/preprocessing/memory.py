"""Guard dense allocation before sparse estimators cross their memory limit."""

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from app.core.config import settings
from app.modules.ml_engine.spec import MLMemoryBudgetExceeded


def bounded_dense(matrix, *, max_bytes: int | None = None):
    limit = min(settings.ML_DENSE_MATRIX_MAX_BYTES, max_bytes or settings.ML_DENSE_MATRIX_MAX_BYTES)
    required = int(matrix.shape[0]) * int(matrix.shape[1]) * np.dtype(matrix.dtype).itemsize
    if required > limit:
        raise MLMemoryBudgetExceeded(
            f"Dense feature matrix needs {required:,} bytes; limit is {limit:,}. "
            "Reduce input rows or categorical features."
        )
    return matrix.toarray() if hasattr(matrix, "toarray") else matrix


def estimator_matrix(matrix, estimator):
    if isinstance(estimator, (HistGradientBoostingClassifier, HistGradientBoostingRegressor)):
        return bounded_dense(matrix)
    return matrix
