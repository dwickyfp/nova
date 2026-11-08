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
    frequency = _aligned_frequency(spec.frequency, frame)
    closed = _closed_weekdays(frame, frequency)
    frame, filled = _regular_periods(
        frame, frequency, str(spec.parameters.get("missing_periods", "interpolate")),
        closed_weekdays=closed,
    )
    if closed:
        names = ", ".join(_WEEKDAYS[day] for day in sorted(closed))
        warnings.append(
            f"No observations on {names} in the whole history: treated as closed, "
            "learned as zero and forecast as zero"
        )
    if filled:
        warnings.append(f"Filled {filled} missing period(s) between observations")
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
        shut = pd.Timestamp(record["ds"]).weekday() in closed
        results.append(
            {
                "series": None if record["unique_id"] == "__single__" else record["unique_id"],
                "timestamp": record["ds"].isoformat(),
                "prediction": 0.0 if shut else float(record[prediction_column]),
                "lower_bound": 0.0 if shut else _float_or_none(record.get(lower)),
                "upper_bound": 0.0 if shut else _float_or_none(record.get(upper)),
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
        "filled_periods": filled,
        "closed_weekdays": [_WEEKDAYS[day] for day in sorted(closed)],
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


def _unit(frequency: str) -> str:
    """The period a pandas alias steps by, whatever it is anchored to."""
    letters = "".join(ch for ch in frequency.upper().split("-")[0] if ch.isalpha())
    return "Y" if letters[:1] in {"Y", "A"} else letters[:1]


def _aligned_frequency(given: str | None, frame: pd.DataFrame) -> str:
    """The requested frequency, anchored where the series itself is.

    A monthly series stamped on the first of the month and forecast with "M"
    would label October's value 30 September: month end, one step early.
    """
    if not given:
        return _infer_frequency(frame)
    try:
        observed = _infer_frequency(frame)
    except ValueError:
        return given
    return observed if _unit(observed) == _unit(given) else given


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _closed_weekdays(frame: pd.DataFrame, frequency: str) -> set[int]:
    """Weekdays a daily history never has, such as a shop closed on Sunday.

    Only judged with at least three full weeks of history and while other weekdays
    are observed; such a day is a closure, not a missing value to interpolate.
    """
    if _unit(frequency) != "D" or frequency.upper().startswith("B"):
        return set()
    span = frame["ds"].max() - frame["ds"].min()
    if span < pd.Timedelta(days=21):
        return set()
    seen = set(frame["ds"].dt.weekday.unique())
    closed = set(range(7)) - {int(day) for day in seen}
    return closed if len(closed) < 7 else set()


def _regular_periods(
    frame: pd.DataFrame, frequency: str, policy: str, *, closed_weekdays: set[int] | None = None,
) -> tuple[pd.DataFrame, int]:
    """Every series on one regular grid: a day with no orders is still a day.

    StatsForecast reads rows as consecutive periods, so a gap silently shortens the
    history and breaks seasonality. Missing periods are interpolated (a level such as
    stock or price) or set to zero (``missing_periods="zero"``, a sum such as units).
    A closed weekday is zero whatever the policy: nothing happened, nothing is missing.
    """
    closed_weekdays = closed_weekdays or set()
    if policy not in {"interpolate", "zero"}:
        raise ValueError("missing_periods must be 'interpolate' or 'zero'")
    parts = []
    filled = 0
    for name, group in frame.groupby("unique_id", sort=False):
        grid = pd.date_range(group["ds"].min(), group["ds"].max(), freq=frequency)
        if not group["ds"].isin(grid).all():
            # Observations off the grid (a misaligned anchor): leave the series as is.
            parts.append(group)
            continue
        regular = group.set_index("ds")[["y"]].reindex(grid)
        if closed_weekdays:
            shut = pd.DatetimeIndex(regular.index).weekday.isin(sorted(closed_weekdays))
            regular.loc[shut & regular["y"].isna(), "y"] = 0.0
        missing = int(regular["y"].isna().sum())
        if missing:
            filled += missing
            regular["y"] = (
                regular["y"].fillna(0) if policy == "zero"
                else regular["y"].interpolate(method="linear")
            )
        regular = regular.rename_axis("ds").reset_index()
        regular["unique_id"] = name
        parts.append(regular[["unique_id", "ds", "y"]])
    return pd.concat(parts, ignore_index=True), filled


def _step_frequency(values: pd.Series) -> str | None:
    """The usual step of a series with gaps: daily rows with some days missing."""
    steps = values.diff().dropna()
    if steps.empty:
        return None
    step = steps.mode().iloc[0]
    if step <= pd.Timedelta(0) or step > pd.Timedelta(days=7):
        return None
    if (steps % step != pd.Timedelta(0)).any():
        return None
    return pd.tseries.frequencies.to_offset(step).freqstr


def _infer_frequency(frame: pd.DataFrame) -> str:
    frequencies: list[str] = []
    for _, group in frame.groupby("unique_id"):
        values = group["ds"].drop_duplicates().sort_values()
        inferred = pd.infer_freq(values) if len(values) >= 3 else None
        if not inferred and len(values) >= 3:
            inferred = _step_frequency(values)
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
