"""Provider-independent text embeddings backed by Nova's AI model registry."""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from app.common.ssrf_guard import BlockedEndpointError, guarded_async_client
from app.core.exceptions import NovaException
from app.modules.ai_ml.service import ai_service


class EmbeddingError(NovaException):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message, status_code=status_code)


@dataclass(frozen=True)
class ResolvedEmbeddingModel:
    model_id: str
    provider_id: str
    provider_model_name: str
    revision: str
    dimensions: int
    modality: str
    metric: str
    endpoint: str


class EmbeddingService:
    """Resolve a pinned model and send bounded batches to a compatible provider."""

    @staticmethod
    def _endpoint(base: str) -> str:
        base = base.rstrip("/")
        return base if base.endswith("/embeddings") else f"{base}/embeddings"

    async def resolve_model(
        self,
        *,
        alias: str | None = None,
        model_id: str | None = None,
        revision: str | None = None,
    ) -> ResolvedEmbeddingModel:
        if (alias is None) == (model_id is None):
            raise EmbeddingError("Specify exactly one embedding alias or model ID")
        models = await ai_service.list_models()
        matches = [
            model
            for model in models
            if model.get("type") == "embedding"
            and model.get("is_active", True)
            and (model.get("logical_alias") == alias if alias else model.get("id") == model_id)
        ]
        if len(matches) != 1:
            raise EmbeddingError("Embedding model is unavailable", status_code=404)
        model = matches[0]
        if revision is not None and model.get("revision") != revision:
            raise EmbeddingError("Pinned embedding model revision is unavailable", status_code=409)
        provider = await ai_service.get_provider(model["provider_id"])
        if not provider or not provider.get("is_active", True):
            raise EmbeddingError("Embedding provider is unavailable", status_code=503)
        if provider.get("type") not in {"openai", "openai_compatible"}:
            raise EmbeddingError("Provider does not support text embeddings")
        return ResolvedEmbeddingModel(
            model_id=model["id"],
            provider_id=model["provider_id"],
            provider_model_name=model["name"],
            revision=model["revision"],
            dimensions=model["dimensions"],
            modality=model.get("modality") or "text",
            metric=model.get("metric") or "cosine",
            endpoint=self._endpoint(provider["endpoint"]),
        )

    async def embed(self, text: str, model: ResolvedEmbeddingModel) -> list[float]:
        return (await self.embed_batch([text], model))[0]

    async def embed_batch(
        self,
        texts: list[str],
        model: ResolvedEmbeddingModel,
    ) -> list[list[float]]:
        if (
            not texts
            or len(texts) > 64
            or any(not isinstance(t, str) or not t.strip() for t in texts)
        ):
            raise EmbeddingError("Provide 1 to 64 non-empty text inputs")
        if model.modality != "text":
            raise EmbeddingError("Only text embeddings are supported")
        key = await ai_service.get_provider_api_key(model.provider_id)
        if not key:
            raise EmbeddingError("Embedding provider credential is unavailable", status_code=503)
        try:
            async with guarded_async_client(timeout=30.0) as client:
                response = await client.post(
                    model.endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model.provider_model_name, "input": texts},
                )
            response.raise_for_status()
            payload = response.json()
        except (BlockedEndpointError, httpx.HTTPError, ValueError) as exc:
            raise EmbeddingError("Embedding provider request failed", status_code=502) from exc
        try:
            rows = sorted(payload["data"], key=lambda item: item["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError("Unexpected embedding response indices")
            vectors = [row["embedding"] for row in rows]
            if any(
                not isinstance(vector, list)
                or len(vector) != model.dimensions
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in vector
                )
                for vector in vectors
            ):
                raise ValueError("Invalid embedding dimensions or values")
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(
                "Embedding provider returned an invalid vector", status_code=502
            ) from exc
        return [[float(value) for value in vector] for vector in vectors]


embedding_service = EmbeddingService()
