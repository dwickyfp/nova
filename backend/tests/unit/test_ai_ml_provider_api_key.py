"""Regression: the assistant must present the *decrypted* provider API key.

The stored key is Fernet ciphertext (``enc:...``). ``get_provider(reveal=True)``
returns the raw column, so ``get_provider_api_key`` has to decrypt — returning
the ciphertext made every outbound call carry ``Bearer enc:...`` and the provider
answered 401 (the assistant surfaced "AI provider returned HTTP 401").
"""

from __future__ import annotations

import pytest

from app.common.crypto import encrypt
from app.modules.ai_ml.service import AIService


@pytest.fixture
def service_with_row(monkeypatch):
    """An ``AIService`` whose ``get_provider`` returns a fixed stored row."""

    def _make(row: dict) -> AIService:
        service = AIService()

        async def fake_get_provider(provider_id: str, *, reveal: bool = False):
            return dict(row)

        monkeypatch.setattr(service, "get_provider", fake_get_provider)
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
    # bogus secret; ``_safe_decrypt`` swallows the failure.
    service = service_with_row({"id": "p1", "api_key": "enc:not-a-valid-token"})
    assert await service.get_provider_api_key("p1") is None
