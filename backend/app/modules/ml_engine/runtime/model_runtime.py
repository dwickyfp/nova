"""Checksummed model loading and vectorized batch inference."""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa

from app.core.config import settings
from app.modules.ml_engine.artifacts.serializer import deserialize_bundle
from app.modules.ml_engine.artifacts.store import ArtifactStore, ObjectArtifactStore
from app.modules.ml_engine.execution.executor import BoundedMLExecutor
from app.modules.ml_engine.registry.repository import (
    ModelRegistryRepository,
    model_registry_repository,
)
from app.modules.ml_engine.runtime.model_cache import ModelCache
from app.modules.ml_engine.spec import InvalidMLSpec


class ModelRuntime:
    def __init__(
        self,
        *,
        repository: ModelRegistryRepository | None = None,
        store: ArtifactStore | None = None,
        cache: ModelCache | None = None,
        executor: BoundedMLExecutor | None = None,
    ) -> None:
        self.repository = repository or model_registry_repository
        self.store = store or ObjectArtifactStore()
        self.cache = cache or ModelCache(
            max_models=settings.ML_MODEL_CACHE_MAX_MODELS,
            max_bytes=settings.ML_MODEL_CACHE_MAX_BYTES,
            ttl_seconds=settings.ML_MODEL_CACHE_TTL_SECONDS,
        )
        self.executor = executor or BoundedMLExecutor(name="nova-ml-inference")

    async def resolve_alias(
        self, alias: str, *, owner_name: str, database_name: str | None
    ) -> dict[str, Any]:
        row = await self.repository.resolve_alias(
            alias, owner_name=owner_name, database_name=database_name
        )
        if row is None:
            raise ValueError(f"Model alias '{alias}' was not found in this scope")
        return row

    async def load(self, metadata: dict[str, Any]) -> dict:
        key = (str(metadata["model_id"]), int(metadata["version"]))

        async def loader() -> tuple[dict, int]:
            artifact_uri = metadata.get("artifact_uri")
            if artifact_uri:
                payload = await asyncio.to_thread(self.store.get, artifact_uri)
                bundle = await self.executor.run(
                    deserialize_bundle, payload, metadata.get("artifact_sha256")
                )
                return bundle, len(payload)
            legacy = metadata.get("model_binary")
            if not legacy:
                raise ValueError("Model version has no artifact")
            payload = base64.b64decode(legacy)
            bundle = await self.executor.run(joblib.load, io.BytesIO(payload))
            if not isinstance(bundle, dict):
                raise ValueError("Legacy model has an invalid bundle")
            return bundle, len(payload)

        return await self.cache.get_or_load(key, loader)

    async def predict_alias(
        self,
        alias: str,
        table: pa.Table,
        *,
        owner_name: str,
        database_name: str | None,
    ) -> tuple[dict[str, Any], list[Any], list[dict[str, float]] | None]:
        metadata = await self.resolve_alias(
            alias, owner_name=owner_name, database_name=database_name
        )
        bundle = await self.load(metadata)
        if bundle.get("task") == "forecast":
            raise InvalidMLSpec("Forecast models require forecast_alias(), not generic prediction")
        result = await self.executor.run(self.predict_bundle_table, bundle, table)
        predictions = result.column("prediction").to_pylist()
        probabilities = (
            result.column("probability").to_pylist()
            if "probability" in result.column_names
            else None
        )
        return metadata, predictions, probabilities

    async def predict_version(
        self,
        model_id: str,
        version: int,
        table: pa.Table,
        *,
        owner_name: str,
        database_name: str | None,
    ) -> tuple[dict[str, Any], list[Any], list[dict[str, float]] | None]:
        """Predict with one immutable model version, bypassing alias movement."""
        metadata = await self.repository.get_version(
            model_id,
            version,
            owner_name=owner_name,
            database_name=database_name,
        )
        if metadata is None:
            raise ValueError(f"Model version '{model_id}:{version}' was not found")
        bundle = await self.load(metadata)
        if bundle.get("task") == "forecast":
            raise InvalidMLSpec(
                "Forecast models require forecast_version(), not generic prediction"
            )
        result = await self.executor.run(self.predict_bundle_table, bundle, table)
        predictions = result.column("prediction").to_pylist()
        probabilities = (
            result.column("probability").to_pylist()
            if "probability" in result.column_names
            else None
        )
        return metadata, predictions, probabilities

    async def predict_alias_table(
        self,
        alias: str,
        table: pa.Table,
        *,
        owner_name: str,
        database_name: str | None,
    ) -> tuple[dict[str, Any], pa.Table]:
        metadata = await self.resolve_alias(
            alias, owner_name=owner_name, database_name=database_name
        )
        bundle = await self.load(metadata)
        if bundle.get("task") == "forecast":
            raise InvalidMLSpec("Forecast models require forecast_alias(), not generic prediction")
        return metadata, await self.executor.run(self.predict_bundle_table, bundle, table)

    async def forecast_alias(
        self,
        alias: str,
        horizon: int,
        *,
        owner_name: str,
        database_name: str | None,
        level: int = 95,
        series: str | None = None,
    ) -> tuple[dict[str, Any], pa.Table]:
        metadata = await self.resolve_alias(
            alias, owner_name=owner_name, database_name=database_name
        )
        bundle = await self.load(metadata)
        return metadata, await self.executor.run(
            self.forecast_bundle_table, bundle, horizon, level, series
        )

    async def forecast_version(
        self,
        model_id: str,
        version: int,
        horizon: int,
        *,
        owner_name: str,
        database_name: str | None,
        level: int = 95,
        series: str | None = None,
    ) -> tuple[dict[str, Any], pa.Table]:
        metadata = await self.repository.get_version(
            model_id, version, owner_name=owner_name, database_name=database_name
        )
        if metadata is None:
            raise ValueError(f"Model version '{model_id}:{version}' was not found")
        bundle = await self.load(metadata)
        return metadata, await self.executor.run(
            self.forecast_bundle_table, bundle, horizon, level, series
        )

    @staticmethod
    def predict_bundle(
        bundle: dict, table: pa.Table
    ) -> tuple[list[Any], list[dict[str, float]] | None]:
        result = ModelRuntime.predict_bundle_table(bundle, table)
        predictions = result.column("prediction").to_pylist()
        probabilities = (
            result.column("probability").to_pylist()
            if "probability" in result.column_names
            else None
        )
        return predictions, probabilities

    @staticmethod
    def predict_bundle_table(bundle: dict, table: pa.Table) -> pa.Table:
        task = bundle.get("task", "regression")
        if task == "forecast":
            raise InvalidMLSpec("Forecast models do not support row prediction")
        preprocessor = bundle.get("preprocessor")
        if preprocessor is None:
            features = bundle.get("feature_columns") or []
            X = table.select(features).to_pandas().to_numpy(dtype=float, copy=False)
        else:
            X = preprocessor.transform(table)
        model = bundle["model"]
        if task == "anomaly_detection" or task == "clustering":
            values = model.predict(X)
        else:
            values = model.predict(X)
        label_encoder = bundle.get("label_encoder")
        if label_encoder is not None:
            values = label_encoder.inverse_transform(values)
        predictions = np.asarray(values)
        probabilities = None
        if task == "classification" and hasattr(model, "predict_proba"):
            raw = model.predict_proba(X)
            classes = (
                label_encoder.inverse_transform(model.classes_)
                if label_encoder is not None
                else model.classes_
            )
            probabilities = [
                {str(name): float(value) for name, value in zip(classes, row, strict=False)}
                for row in raw
            ]
        result = table.append_column("prediction", pa.array(predictions))
        if probabilities is not None:
            result = result.append_column("probability", pa.array(probabilities))
        return result

    @staticmethod
    def forecast_bundle_table(
        bundle: dict,
        horizon: int,
        level: int = 95,
        series: str | None = None,
    ) -> pa.Table:
        if bundle.get("task") != "forecast":
            raise InvalidMLSpec("Only persisted forecast models support forecast inference")
        if horizon < 1:
            raise InvalidMLSpec("forecast horizon must be at least 1")
        model = bundle["model"]
        frame = model.predict(h=horizon, level=[level])
        if series is not None:
            frame = frame.loc[frame["unique_id"].astype(str) == series]
            if frame.empty:
                raise InvalidMLSpec(f"Forecast series '{series}' was not found")
        algorithm = str(bundle.get("algorithm") or "")
        prediction_column = (
            algorithm
            if algorithm in frame.columns
            else next(
                name
                for name in frame.columns
                if name not in {"unique_id", "ds"} and "-lo-" not in name and "-hi-" not in name
            )
        )
        lower = f"{prediction_column}-lo-{level}"
        upper = f"{prediction_column}-hi-{level}"
        output = pd.DataFrame(
            {
                "timestamp": frame["ds"],
                "series": frame["unique_id"].replace({"__single__": None}),
                "prediction": frame[prediction_column],
                "lower": frame.get(lower, None),
                "upper": frame.get(upper, None),
            }
        )
        return pa.Table.from_pandas(output, preserve_index=False)
