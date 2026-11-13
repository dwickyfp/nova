"""Only the credential reader decrypts keys for outbound provider requests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.common.crypto import encrypt
from app.modules.ai_ml import service as service_module
from app.modules.ai_ml.repository import ProviderCredential
from app.modules.ai_ml.service import AIService


@pytest.fixture
def service_with_row(monkeypatch, fake_metadata_db):

    def _make(row: dict) -> AIService:
        service = AIService()

        registry = AsyncMock()
        registry.runtime_credential.return_value = ProviderCredential(
            provider={"id": row["id"]}, ciphertext=row["api_key"],
        )
        monkeypatch.setattr(service_module, "AIRegistryRepository", lambda connection: registry)
        return service

    return _make


async def test_api_key_is_decrypted_not_returned_as_ciphertext(service_with_row):
    stored = encrypt("sk-live-secret")
    assert stored.startswith("enc:")

    service = service_with_row({"id": "p1", "api_key": stored})
    key = await service.get_provider_api_key("p1")

    assert key == "sk-live-secret"
    assert not key.startswith("enc:")


async def test_missing_key_returns_none(service_with_row):
    service = service_with_row({"id": "p1", "api_key": None})
    assert await service.get_provider_api_key("p1") is None


async def test_undecryptable_ciphertext_returns_none(service_with_row):
    # A key stored under a different FERNET_KEY must not be handed out as a
    # bogus secret; the credential reader refuses a decryption failure.
    service = service_with_row({"id": "p1", "api_key": "enc:not-a-valid-token"})
    assert await service.get_provider_api_key("p1") is None
