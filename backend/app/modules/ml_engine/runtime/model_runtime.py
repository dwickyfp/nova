"""Checksummed model loading and vectorized batch inference."""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any

import joblib
import numpy as np
import pyarrow as pa

from app.core.config import settings
from app.modules.ml_engine.artifacts.serializer import deserialize_bundle
from app.modules.ml_engine.artifacts.store import ArtifactStore, ObjectArtifactStore
from app.modules.ml_engine.registry.repository import (
    ModelRegistryRepository,
    model_registry_repository,
)
from app.modules.ml_engine.runtime.model_cache import ModelCache


class ModelRuntime:
    def __init__(
        self,
        *,
        repository: ModelRegistryRepository | None = None,
        store: ArtifactStore | None = None,
        cache: ModelCache | None = None,
    ) -> None:
        self.repository = repository or model_registry_repository
        self.store = store or ObjectArtifactStore()
        self.cache = cache or ModelCache(
            max_models=settings.ML_MODEL_CACHE_MAX_MODELS,
            max_bytes=settings.ML_MODEL_CACHE_MAX_BYTES,
            ttl_seconds=settings.ML_MODEL_CACHE_TTL_SECONDS,
        )

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
                bundle = deserialize_bundle(payload, metadata.get("artifact_sha256"))
                return bundle, len(payload)
            legacy = metadata.get("model_binary")
            if not legacy:
                raise ValueError("Model version has no artifact")
            payload = base64.b64decode(legacy)
            bundle = joblib.load(io.BytesIO(payload))
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
        predictions, probabilities = self.predict_bundle(bundle, table)
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
        predictions, probabilities = self.predict_bundle(bundle, table)
        return metadata, predictions, probabilities

    @staticmethod
    def predict_bundle(
        bundle: dict, table: pa.Table
    ) -> tuple[list[Any], list[dict[str, float]] | None]:
        task = bundle.get("task", "regression")
        preprocessor = bundle.get("preprocessor")
        if preprocessor is None:
            features = bundle.get("feature_columns") or []
            X = np.asarray(
                [[row.get(name) for name in features] for row in table.to_pylist()],
                dtype=float,
            )
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
        predictions = [item.item() if isinstance(item, np.generic) else item for item in values]
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
        return predictions, probabilities
