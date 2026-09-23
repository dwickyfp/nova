"""Chronological multi-series forecasting with StatsForecast."""

from __future__ import annotations

import time

import pandas as pd
import pyarrow as pa

from app.core.config import settings
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.spec import DataBudgetExceeded, InsufficientTrainingRows, MLExecutionSpec


def train_forecast(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    from statsforecast import StatsForecast

    timestamp = str(spec.timestamp_column)
    target = str(spec.target_column)
    for column in (timestamp, target):
        if column not in table.column_names:
            raise ValueError(f"Forecast column '{column}' is not present")
    conversion_started = time.perf_counter()
    frame = table.to_pandas()
    arrow_to_pandas_seconds = time.perf_counter() - conversion_started
    frame[timestamp] = pd.to_datetime(frame[timestamp], errors="coerce", utc=True)
    frame[target] = pd.to_numeric(frame[target], errors="coerce")
    invalid_rows = int(frame[[timestamp, target]].isna().any(axis=1).sum())
    if invalid_rows:
        raise ValueError(
            f"Forecast input contains {invalid_rows} row(s) with a missing/invalid "
            "timestamp or target"
        )
    frame = frame.dropna(subset=[timestamp, target])
    series = spec.series_column
    if series:
        if series not in frame:
            raise ValueError(f"Series column '{series}' is not present")
        frame["unique_id"] = frame[series].astype(str)
    else:
        frame["unique_id"] = "__single__"
    frame = frame.rename(columns={timestamp: "ds", target: "y"})
    frame = frame[["unique_id", "ds", "y"]].sort_values(["unique_id", "ds"])
    horizon = int(spec.horizon or 1)
    minimum_required = max(10, horizon + 2)
    sizes = frame.groupby("unique_id").size()
    insufficient = [str(name) for name, size in sizes.items() if int(size) < minimum_required]
    warnings: list[str] = []
    if insufficient:
        policy = str(spec.parameters.get("insufficient_series_policy", "reject"))
        if policy == "drop":
            frame = frame[~frame["unique_id"].isin(insufficient)]
            warnings.append("Dropped insufficient series: " + ", ".join(sorted(insufficient)))
            if frame.empty:
                raise InsufficientTrainingRows("No forecast series has enough observations")
        elif policy == "reject":
            raise InsufficientTrainingRows(
                "Forecast series require at least "
                f"{minimum_required} observations; insufficient: " + ", ".join(sorted(insufficient))
            )
        else:
            raise ValueError("insufficient_series_policy must be 'reject' or 'drop'")
    duplicates = frame.duplicated(["unique_id", "ds"]).sum()
    if duplicates:
        raise ValueError(f"Forecast input contains {int(duplicates)} duplicate timestamp(s)")
    frequency = spec.frequency or _infer_frequency(frame)
    if int(frame["unique_id"].nunique()) * horizon > settings.ML_RESULT_INLINE_MAX_ROWS:
        raise DataBudgetExceeded(
            "Forecast output exceeds the inline row budget; reduce series or horizon"
        )
    minimum_series_rows = int(frame.groupby("unique_id").size().min())
    validation_horizon = min(horizon, max(1, minimum_series_rows // 5))
    cutoff = frame.groupby("unique_id").tail(validation_horizon).index
    train = frame.drop(index=cutoff)
    validation = frame.loc[cutoff]
    if train.empty:
        raise InsufficientTrainingRows("Not enough chronological history for validation")

    models = _models_for(spec, frequency)
    validator = StatsForecast(models=models, freq=frequency, n_jobs=1)
    validator.fit(train)
    validation_predictions = validator.predict(h=validation_horizon)
    algorithm, validation_mae = _best_forecast_column(validation, validation_predictions)

    selected_model = next(model for model in models if repr(model) == algorithm)
    final = StatsForecast(models=[selected_model], freq=frequency, n_jobs=1)
    final.fit(frame)
    forecast = final.predict(h=horizon, level=[95])
    prediction_column = repr(selected_model)
    results = []
    lower = f"{prediction_column}-lo-95"
    upper = f"{prediction_column}-hi-95"
    for record in forecast.head(1000).to_dict(orient="records"):
        results.append(
            {
                "series": None if record["unique_id"] == "__single__" else record["unique_id"],
                "timestamp": record["ds"].isoformat(),
                "prediction": float(record[prediction_column]),
                "lower_bound": _float_or_none(record.get(lower)),
                "upper_bound": _float_or_none(record.get(upper)),
            }
        )
    metrics = {
        "result_rows": len(forecast),
        "validation_mae": validation_mae,
        "validation_strategy": "chronological_holdout",
        "validation_horizon": validation_horizon,
        "frequency": frequency,
        "horizon": horizon,
        "series_count": int(frame["unique_id"].nunique()),
        "training_end": train["ds"].max().isoformat(),
        "validation_start": validation["ds"].min().isoformat(),
        "selected_estimator": algorithm,
        "warnings": warnings,
        "dropped_series": insufficient if warnings else [],
        "arrow_to_pandas_seconds": arrow_to_pandas_seconds,
    }
    bundle = {
        "task": "forecast",
        "model": final,
        "feature_columns": [],
        "target_column": target,
        "timestamp_column": timestamp,
        "series_column": series,
        "frequency": frequency,
        "algorithm": algorithm,
    }
    return TrainingOutput(
        bundle=bundle,
        engine="statsforecast",
        algorithm=algorithm,
        metrics=metrics,
        feature_columns=[],
        training_rows=len(frame),
        results=results,
    )


def _models_for(spec: MLExecutionSpec, frequency: str):
    from statsforecast.models import AutoARIMA, AutoETS, HistoricAverage, Naive, SeasonalNaive

    if spec.algorithm != "auto":
        choices = {
            "naive": Naive(),
            "seasonal_naive": SeasonalNaive(season_length=_season_length(frequency)),
            "auto_ets": AutoETS(season_length=_season_length(frequency)),
            "auto_arima": AutoARIMA(season_length=_season_length(frequency)),
        }
        if spec.algorithm not in choices:
            raise ValueError(f"Unsupported forecast algorithm: {spec.algorithm}")
        return [choices[spec.algorithm]]
    models = [Naive(), HistoricAverage()]
    if spec.mode.value in {"balanced", "best"}:
        models.append(AutoETS(season_length=_season_length(frequency)))
    if spec.mode.value == "best":
        models.append(AutoARIMA(season_length=_season_length(frequency)))
    return models


def _infer_frequency(frame: pd.DataFrame) -> str:
    frequencies: list[str] = []
    for _, group in frame.groupby("unique_id"):
        values = group["ds"].drop_duplicates().sort_values()
        inferred = pd.infer_freq(values) if len(values) >= 3 else None
        if inferred:
            frequencies.append(inferred)
    if not frequencies:
        raise ValueError("Could not infer forecast frequency; provide frequency explicitly")
    if len(set(frequencies)) != 1:
        raise ValueError("Series have inconsistent frequencies; provide normalized input")
    return frequencies[0]


def _season_length(frequency: str) -> int:
    normalized = frequency.upper()
    if normalized.startswith(("D", "B")):
        return 7
    if normalized.startswith("H"):
        return 24
    if normalized.startswith(("M", "MS")):
        return 12
    if normalized.startswith("Q"):
        return 4
    return 1


def _best_forecast_column(validation: pd.DataFrame, predictions: pd.DataFrame) -> tuple[str, float]:
    merged = validation.merge(predictions, on=["unique_id", "ds"], how="inner")
    candidates = [
        name
        for name in predictions.columns
        if name not in {"unique_id", "ds"} and "-lo-" not in name and "-hi-" not in name
    ]
    scores = {name: float((merged["y"] - merged[name]).abs().mean()) for name in candidates}
    return min(scores.items(), key=lambda item: item[1])


def _float_or_none(value):
    return None if value is None or pd.isna(value) else float(value)
