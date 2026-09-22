"""Budget-aware candidate search for tabular classification and regression."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.base import clone
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, r2_score

from app.modules.ml_engine.automl.flaml_engine import train_flaml
from app.modules.ml_engine.spec import MLMode, MLTask


@dataclass
class AutoMLSelection:
    estimator: Any
    name: str
    score: float
    metric: str
    duration_seconds: float
    candidates_evaluated: int
    hyperparameters: dict[str, Any]
    backend: str = "sklearn"
    loss_value: float | None = None


class AutoMLRouter:
    """Evaluate real candidate models under a hard wall-clock search budget."""

    def __init__(self, *, random_seed: int = 42) -> None:
        self.random_seed = random_seed

    def select(
        self,
        *,
        task: MLTask,
        mode: MLMode,
        X_train,
        y_train,
        X_valid,
        y_valid,
        timeout_seconds: float,
        algorithm: str = "auto",
        metric: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> AutoMLSelection:
        started = time.monotonic()
        if algorithm == "auto":
            estimator_list = self._flaml_estimators(task, mode)
            estimator, metadata = train_flaml(
                X_train,
                y_train,
                X_valid=X_valid,
                y_valid=y_valid,
                task=task.value,
                metric=metric or ("accuracy" if task is MLTask.CLASSIFICATION else "r2"),
                timeout_seconds=timeout_seconds,
                random_seed=self.random_seed,
                estimator_list=estimator_list,
            )
            return AutoMLSelection(
                estimator=estimator,
                name=str(metadata["selected_estimator"]),
                score=float("nan"),
                metric=metric or ("accuracy" if task is MLTask.CLASSIFICATION else "r2"),
                duration_seconds=time.monotonic() - started,
                candidates_evaluated=int(metadata["candidates_evaluated"]),
                hyperparameters=_json_parameters(metadata["hyperparameters"]),
                backend="flaml",
                loss_value=float(metadata["validation_loss"]),
            )
        candidates = self._candidates(task, mode, algorithm, parameters or {})
        metric = "weighted_f1" if task is MLTask.CLASSIFICATION else "r2"
        best: tuple[float, str, Any, dict[str, Any]] | None = None
        evaluated = 0
        failures: list[str] = []
        for name, estimator in candidates:
            if evaluated and time.monotonic() - started >= timeout_seconds:
                break
            try:
                fitted = clone(estimator).fit(X_train, y_train)
                predicted = fitted.predict(X_valid)
                score = (
                    float(f1_score(y_valid, predicted, average="weighted", zero_division=0))
                    if task is MLTask.CLASSIFICATION
                    else float(r2_score(y_valid, predicted))
                )
                evaluated += 1
                params = fitted.get_params(deep=False)
                if best is None or score > best[0]:
                    best = (score, name, fitted, params)
            except Exception as exc:
                failures.append(f"{name}: {type(exc).__name__}")
        if best is None:
            raise ValueError("No AutoML candidate could be trained: " + "; ".join(failures))
        return AutoMLSelection(
            estimator=best[2],
            name=best[1],
            score=best[0],
            metric=metric,
            duration_seconds=time.monotonic() - started,
            candidates_evaluated=evaluated,
            hyperparameters=_json_parameters(best[3]),
        )

    @staticmethod
    def _flaml_estimators(task: MLTask, mode: MLMode) -> list[str]:
        if task is MLTask.CLASSIFICATION:
            candidates = ["lrl1", "rf", "extra_tree", "lgbm"]
        else:
            candidates = ["rf", "extra_tree", "lgbm", "xgboost"]
        limit = {MLMode.INTERACTIVE: 2, MLMode.BALANCED: 3, MLMode.BEST: 4}[mode]
        return candidates[:limit]

    def _candidates(
        self,
        task: MLTask,
        mode: MLMode,
        algorithm: str,
        parameters: dict[str, Any],
    ) -> list[tuple[str, Any]]:
        seed = self.random_seed
        if task is MLTask.CLASSIFICATION:
            all_candidates = [
                ("logistic", LogisticRegression(max_iter=1000, random_state=seed)),
                (
                    "random_forest",
                    RandomForestClassifier(n_estimators=120, random_state=seed, n_jobs=1),
                ),
                (
                    "extra_trees",
                    ExtraTreesClassifier(n_estimators=120, random_state=seed, n_jobs=1),
                ),
                ("hist_gradient_boost", HistGradientBoostingClassifier(random_state=seed)),
            ]
        else:
            all_candidates = [
                ("ridge", Ridge()),
                (
                    "random_forest",
                    RandomForestRegressor(n_estimators=120, random_state=seed, n_jobs=1),
                ),
                ("extra_trees", ExtraTreesRegressor(n_estimators=120, random_state=seed, n_jobs=1)),
                ("hist_gradient_boost", HistGradientBoostingRegressor(random_state=seed)),
            ]
        if algorithm != "auto":
            aliases = {
                "linear": "ridge",
                "logistic": "logistic",
                "gradient_boost": "hist_gradient_boost",
            }
            wanted = aliases.get(algorithm, algorithm)
            selected = [(name, estimator) for name, estimator in all_candidates if name == wanted]
            if not selected:
                raise ValueError(f"Algorithm '{algorithm}' is not available for {task.value}")
            if parameters:
                selected[0][1].set_params(**parameters)
            return selected
        limit = {MLMode.INTERACTIVE: 2, MLMode.BALANCED: 3, MLMode.BEST: 4}[mode]
        return all_candidates[:limit]


def evaluate_predictions(task: MLTask, truth, predicted) -> dict[str, float]:
    if task is MLTask.CLASSIFICATION:
        return {
            "accuracy": float(accuracy_score(truth, predicted)),
            "weighted_f1": float(f1_score(truth, predicted, average="weighted", zero_division=0)),
        }
    return {
        "mae": float(mean_absolute_error(truth, predicted)),
        "r2": float(r2_score(truth, predicted)),
        "rmse": float(np.sqrt(np.mean((np.asarray(truth) - np.asarray(predicted)) ** 2))),
    }


def _json_parameters(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result
