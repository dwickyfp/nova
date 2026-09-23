"""Embedding resolution and provider response validation are deterministic."""

from dataclasses import replace

import httpx
import pytest

from app.modules.ai_ml.embeddings import EmbeddingError, EmbeddingService, ResolvedEmbeddingModel

MODEL = {
    "id": "model-1",
    "provider_id": "provider-1",
    "name": "text-embedding-model",
    "type": "embedding",
    "logical_alias": "nova.embedding.default",
    "revision": "rev-1",
    "dimensions": 3,
    "modality": "text",
    "metric": "cosine",
    "is_active": True,
}


@pytest.fixture
def service(monkeypatch):
    from app.modules.ai_ml import embeddings

    async def list_models():
        return [MODEL]

    async def get_provider(_id):
        return {"type": "openai", "endpoint": "https://api.example.com/v1", "is_active": True}

    async def get_key(_id):
        return "secret"

    monkeypatch.setattr(embeddings.ai_service, "list_models", list_models)
    monkeypatch.setattr(embeddings.ai_service, "get_provider", get_provider)
    monkeypatch.setattr(embeddings.ai_service, "get_provider_api_key", get_key)
    return EmbeddingService()


async def test_resolves_pinned_model(service):
    model = await service.resolve_model(alias="nova.embedding.default", revision="rev-1")
    assert model.model_id == "model-1"
    assert model.endpoint == "https://api.example.com/v1/embeddings"
    assert model.dimensions == 3


async def test_rejects_revision_drift(service):
    with pytest.raises(EmbeddingError, match="revision is unavailable"):
        await service.resolve_model(model_id="model-1", revision="rev-2")


async def test_rejects_unknown_or_ambiguous_selection(service):
    with pytest.raises(EmbeddingError):
        await service.resolve_model()
    with pytest.raises(EmbeddingError):
        await service.resolve_model(alias="nova.embedding.default", model_id="model-1")
    with pytest.raises(EmbeddingError):
        await service.resolve_model(alias="unknown")


class FakeResponse:
    def __init__(self, vectors):
        self.vectors = vectors

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": [
                {"index": index, "embedding": vector} for index, vector in enumerate(self.vectors)
            ]
        }


class FakeClient:
    def __init__(self, vectors):
        self.vectors = vectors
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, *, headers, json):
        self.request = (url, headers, json)
        return FakeResponse(self.vectors)


def resolved():
    return ResolvedEmbeddingModel(
        "model-1",
        "provider-1",
        "text-embedding-model",
        "rev-1",
        3,
        "text",
        "cosine",
        "https://api.example.com/v1/embeddings",
    )


async def test_batch_preserves_order_and_uses_registered_provider(service, monkeypatch):
    from app.modules.ai_ml import embeddings

    client = FakeClient([[1, 0, 0], [0, 1, 0]])
    monkeypatch.setattr(embeddings, "guarded_async_client", lambda **_: client)
    vectors = await service.embed_batch(["first", "second"], resolved())
    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert client.request[2] == {"model": "text-embedding-model", "input": ["first", "second"]}


@pytest.mark.parametrize("vectors", [[[1, 2]], [[float("nan"), 2, 3]]])
async def test_invalid_provider_vector_fails_closed(service, monkeypatch, vectors):
    from app.modules.ai_ml import embeddings

    monkeypatch.setattr(embeddings, "guarded_async_client", lambda **_: FakeClient(vectors))
    with pytest.raises(EmbeddingError, match="invalid vector"):
        await service.embed("text", resolved())


@pytest.mark.parametrize("texts", [[], [""], ["ok"] * 65])
async def test_invalid_batch_is_rejected(service, texts):
    with pytest.raises(EmbeddingError, match="1 to 64"):
        await service.embed_batch(texts, resolved())


@pytest.mark.parametrize(
    "provider,expected",
    [
        (None, "Embedding provider is unavailable"),
        ({"type": "openai", "is_active": False}, "Embedding provider is unavailable"),
        ({"type": "anthropic", "is_active": True}, "does not support text embeddings"),
    ],
)
async def test_unavailable_or_unsupported_provider_fails_closed(
    service, monkeypatch, provider, expected
):
    from app.modules.ai_ml import embeddings

    async def get_provider(_id):
        return provider

    monkeypatch.setattr(embeddings.ai_service, "get_provider", get_provider)
    with pytest.raises(EmbeddingError, match=expected):
        await service.resolve_model(alias="nova.embedding.default")


async def test_non_text_model_and_missing_credential_fail_before_http(service, monkeypatch):
    from app.modules.ai_ml import embeddings

    with pytest.raises(EmbeddingError, match="Only text"):
        await service.embed("x", replace(resolved(), modality="image"))

    async def no_key(_id):
        return None

    monkeypatch.setattr(embeddings.ai_service, "get_provider_api_key", no_key)
    with pytest.raises(EmbeddingError, match="credential is unavailable"):
        await service.embed("x", resolved())


async def test_provider_timeout_is_sanitized(service, monkeypatch):
    from app.modules.ai_ml import embeddings

    class TimeoutClient(FakeClient):
        async def post(self, *_args, **_kwargs):
            raise httpx.ConnectTimeout("Bearer secret should not escape")

    monkeypatch.setattr(embeddings, "guarded_async_client", lambda **_: TimeoutClient([]))
    with pytest.raises(EmbeddingError, match="Embedding provider request failed") as failure:
        await service.embed("text", resolved())
    assert "secret" not in str(failure.value)
    assert failure.value.status_code == 502


async def test_missing_response_index_is_rejected(service, monkeypatch):
    from app.modules.ai_ml import embeddings

    class MissingIndexResponse(FakeResponse):
        def json(self):
            return {"data": [{"index": 1, "embedding": [1, 0, 0]}]}

    class MissingIndexClient(FakeClient):
        async def post(self, *_args, **_kwargs):
            return MissingIndexResponse([])

    monkeypatch.setattr(embeddings, "guarded_async_client", lambda **_: MissingIndexClient([]))
    with pytest.raises(EmbeddingError, match="invalid vector"):
        await service.embed("text", resolved())
