from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from app.common import crypto
from app.modules.ai_ml.repository import ModelDefinition, ProviderDefinition


@pytest.fixture
def definition(monkeypatch):
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    return ProviderDefinition(
        "Test", "openai_compatible", "https://example.test/v1", crypto.encrypt("synthetic-key"),
    )


@pytest.mark.parametrize("changes", [
    {"name": ""}, {"name": "x" * 129}, {"name": "bad\x00name"},
    {"type": ""}, {"type": "x" * 33}, {"is_active": 1},
    {"endpoint": "file:///etc/passwd"}, {"endpoint": "https://user:secret@example.test"},
    {"endpoint": "https://"}, {"endpoint": "\ud800"},
    {"encrypted_api_key": "plaintext-canary"}, {"encrypted_api_key": "enc:invalid"},
    {"encrypted_api_key": "enc:" + "x" * 4096},
    {"default_params": "NaN"}, {"default_params": "[]"}, {"default_params": "null"},
    {"default_params": '{"value":NaN}'}, {"default_params": '{"x":1,"x":2}'},
    {"default_params": '{"x":'}, {"default_params": "\ud800"},
    {"default_params": '{"x":["\\ud800"]}'}, {"default_params": '{"x":"\\u0000"}'},
    {"default_params": '{"x":1e999}'},
])
def test_invalid_provider_definition_is_refused_without_value_echo(definition, changes):
    with pytest.raises(ValueError) as error:
        replace(definition, **changes)
    assert "plaintext-canary" not in str(error.value)
    assert "user:secret" not in str(error.value)


def test_wrong_key_and_oversized_plaintext_are_refused(definition):
    other_key = "enc:" + Fernet(Fernet.generate_key()).encrypt(b"synthetic").decode()
    for ciphertext in (other_key, crypto.encrypt("x" * 513), crypto.encrypt("\x00")):
        with pytest.raises(ValueError):
            replace(definition, encrypted_api_key=ciphertext)


@pytest.mark.parametrize("changes", [
    {"provider_id": ""}, {"provider_id": "x" * 65}, {"name": ""},
    {"name": "bad\x00name"}, {"type": "unknown"}, {"is_active": "true"},
    {"max_tokens": True}, {"max_tokens": 0}, {"max_tokens": "123"}, {"max_tokens": 2**31},
    {"dimensions": 128}, {"logical_alias": "unexpected"},
    {"default_params": '{"x":Infinity}'},
])
def test_invalid_model_definition_is_refused(changes):
    model = ModelDefinition("provider", "model", "llm", max_tokens=128)
    with pytest.raises(ValueError):
        replace(model, **changes)


def test_opaque_model_revision_and_parameters_are_preserved():
    raw = '{ "temperature" : 0.2500, "nested" : {"label":"漢字"} }'
    model = ModelDefinition(
        "provider", "model", "embedding", logical_alias="alias", revision="vendor:release+1",
        dimensions=64, modality="text", metric="cosine", default_params=raw,
    )
    assert model.revision == "vendor:release+1"
    assert model.default_params == raw
    assert raw not in repr(model)
