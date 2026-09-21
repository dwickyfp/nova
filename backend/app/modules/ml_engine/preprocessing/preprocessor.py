"""Serializable mixed-type preprocessing shared by training and inference."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyarrow as pa
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.modules.ml_engine.preprocessing.profiler import FeatureProfile, profile_table
from app.modules.ml_engine.spec import InferenceSchemaMismatch, UnsupportedFeatureType


@dataclass
class FeaturePreprocessor:
    feature_columns: list[str]
    scale_numeric: bool = True
    max_categories: int = 100
    profiles: list[FeatureProfile] = field(default_factory=list)
    transformer: ColumnTransformer | None = None
    expanded_columns: list[str] = field(default_factory=list)

    def fit_transform(self, table: pa.Table):
        self._validate_columns(table)
        self.profiles = profile_table(table, self.feature_columns)
        unsupported = [item.name for item in self.profiles if item.kind == "unsupported"]
        if unsupported:
            raise UnsupportedFeatureType("Unsupported ML feature types: " + ", ".join(unsupported))
        frame = self._frame(table)
        numeric = [item.name for item in self.profiles if item.kind == "numeric"]
        boolean = [item.name for item in self.profiles if item.kind == "boolean"]
        categorical = [item.name for item in self.profiles if item.kind == "categorical"]
        datetime_columns = [item.name for item in self.profiles if item.kind == "datetime"]
        expanded_datetime = self._datetime_columns(datetime_columns)
        numeric += expanded_datetime
        self.expanded_columns = list(frame.columns)

        numeric_steps: list[tuple[str, object]] = [
            ("impute", SimpleImputer(strategy="median")),
        ]
        if self.scale_numeric:
            numeric_steps.append(("scale", StandardScaler()))
        categorical_pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="most_frequent")),
                (
                    "encode",
                    OneHotEncoder(
                        handle_unknown="ignore",
                        max_categories=self.max_categories,
                        sparse_output=True,
                    ),
                ),
            ]
        )
        transformers = []
        if numeric:
            transformers.append(("numeric", Pipeline(numeric_steps), numeric))
        if categorical or boolean:
            transformers.append(("categorical", categorical_pipeline, categorical + boolean))
        if not transformers:
            raise UnsupportedFeatureType("No supported feature columns were found")
        self.transformer = ColumnTransformer(transformers, remainder="drop")
        return self.transformer.fit_transform(frame)

    def transform(self, table: pa.Table):
        self._validate_columns(table)
        if self.transformer is None:
            raise RuntimeError("FeaturePreprocessor has not been fitted")
        return self.transformer.transform(self._frame(table))

    def _validate_columns(self, table: pa.Table) -> None:
        missing = [name for name in self.feature_columns if name not in table.column_names]
        if missing:
            raise InferenceSchemaMismatch("Missing feature columns: " + ", ".join(missing))

    def _frame(self, table: pa.Table) -> pd.DataFrame:
        frame = table.select(self.feature_columns).to_pandas()
        datetime_names = [item.name for item in self.profiles if item.kind == "datetime"]
        for name in datetime_names:
            values = pd.to_datetime(frame.pop(name), errors="coerce", utc=True)
            frame[f"{name}__year"] = values.dt.year
            frame[f"{name}__month"] = values.dt.month
            frame[f"{name}__day"] = values.dt.day
            frame[f"{name}__dayofweek"] = values.dt.dayofweek
            frame[f"{name}__hour"] = values.dt.hour
        for item in self.profiles:
            if item.kind in {"categorical", "boolean"} and item.name in frame:
                # sklearn's object imputers understand ``np.nan``; pandas'
                # nullable ``pd.NA`` has three-valued equality and makes the
                # missing-value mask ambiguous.
                values = frame[item.name].astype("string").astype(object)
                frame[item.name] = values.where(pd.notna(values), np.nan)
        return frame.replace([np.inf, -np.inf], np.nan)

    @staticmethod
    def _datetime_columns(columns: list[str]) -> list[str]:
        suffixes = ("year", "month", "day", "dayofweek", "hour")
        return [f"{name}__{suffix}" for name in columns for suffix in suffixes]
