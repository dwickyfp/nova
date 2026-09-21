"""Optional FLAML adapter for deployments that select the FLAML backend."""

from __future__ import annotations

from typing import Any


def train_flaml(
    X_train,
    y_train,
    *,
    X_valid,
    y_valid,
    task: str,
    metric: str | None,
    timeout_seconds: float,
    random_seed: int,
    estimator_list: list[str],
) -> tuple[Any, dict[str, Any]]:
    from flaml import AutoML

    automl = AutoML()
    automl.fit(
        X_train=X_train,
        y_train=y_train,
        X_val=X_valid,
        y_val=y_valid,
        task="classification" if task == "classification" else "regression",
        metric=metric or "auto",
        time_budget=max(1, int(timeout_seconds)),
        estimator_list=estimator_list,
        eval_method="holdout",
        n_jobs=1,
        seed=random_seed,
        verbose=0,
    )
    history = getattr(automl, "config_history", {})
    return automl, {
        "selected_estimator": automl.best_estimator,
        "validation_loss": float(automl.best_loss),
        "hyperparameters": dict(automl.best_config),
        "candidates_evaluated": len(history),
    }
