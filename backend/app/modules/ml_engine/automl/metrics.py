"""Canonical directions for reported model validation metrics."""

METRIC_DIRECTIONS = {
    "accuracy": True,
    "f1": True,
    "weighted_f1": True,
    "roc_auc": True,
    "r2": True,
    "silhouette": True,
    "rmse": False,
    "mae": False,
    "mse": False,
    "log_loss": False,
    "mape": False,
}


def higher_is_better(metric: str) -> bool:
    if metric not in METRIC_DIRECTIONS:
        raise ValueError(f"Unsupported validation metric: {metric}")
    return METRIC_DIRECTIONS[metric]
