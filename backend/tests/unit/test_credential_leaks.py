"""Regression tests for hardcoded credential leaks.

Invariant under test (AGENTS.md): credentials come from ``nova.yaml`` / env and
never appear as literals in code, FILES() injection, or API responses.
"""

import inspect

import pytest

from app.common.crypto import decrypt, encrypt, mask_secret
from app.core.config import StorageConnectionConfig
from app.modules.query.dialect import injector

# Values that must never be baked into source as a fallback.
LEAKED_LITERALS = ("minioadmin", "miniopassword")

CREDENTIAL_MODULES = [
    "app.modules.query.dialect.injector",
    "app.modules.query.service",
    "app.modules.stages.service",
    "app.modules.workspaces.service",
    # NOVA-58: the secret-reference layer must not carry a literal either.
    "app.storage.secrets",
]


@pytest.mark.parametrize("module_name", CREDENTIAL_MODULES)
@pytest.mark.parametrize("literal", LEAKED_LITERALS)
def test_no_hardcoded_credential_literals_in_source(module_name, literal):
    """No module may carry a hardcoded storage credential."""
    module = __import__(module_name, fromlist=["__file__"])
    source = inspect.getsource(module)
    assert literal not in source, f"{literal!r} hardcoded in {module_name}"


class TestCredentialParamsFromConfig:
    """get_credential_params must read nova.yaml, not a constant."""

    def test_credentials_come_from_config(self, monkeypatch):
        monkeypatch.setattr(
            injector,
            "resolve_storage_credentials",
            lambda storage_connection=None: ("cfg-access", "cfg-secret"),
        )
        params = injector.get_credential_params("s3")
        assert params["aws.s3.access_key"] == "cfg-access"
        assert params["aws.s3.secret_key"] == "cfg-secret"

    def test_params_never_contain_the_old_defaults(self, monkeypatch):
        monkeypatch.setattr(
            injector,
            "resolve_storage_credentials",
            lambda storage_connection=None: ("cfg-access", "cfg-secret"),
        )
        params = injector.get_credential_params("s3")
        assert "minioadmin" not in str(params.values())
        assert "miniopassword" not in str(params.values())

    def test_unconfigured_storage_returns_no_credentials(self, monkeypatch):
        """Fail closed: no config means no credential params, not a default."""
        monkeypatch.setattr(
            injector,
            "resolve_storage_credentials",
            lambda storage_connection=None: ("", ""),
        )
        assert injector.get_credential_params("s3") == {}

    def test_injection_skipped_when_no_credentials_configured(self, monkeypatch):
        monkeypatch.setattr(
            injector,
            "resolve_storage_credentials",
            lambda storage_connection=None: ("", ""),
        )
        sql = "SELECT * FROM FILES('path'='s3://b/k', 'format'='csv')"
        assert injector.inject_credentials_into_files(sql) == sql

    def test_unsupported_storage_type_has_no_credentials(self):
        assert injector.get_credential_params("azure") == {}
        assert injector.get_credential_params("gcs") == {}

    def test_named_connection_is_forwarded(self, monkeypatch):
        seen = {}

        def fake_resolve(storage_connection=None):
            seen["name"] = storage_connection
            return ("a", "b")

        monkeypatch.setattr(injector, "resolve_storage_credentials", fake_resolve)
        injector.get_credential_params("s3", storage_connection="backup")
        assert seen["name"] == "backup"


class TestResolveStorageCredentials:
    def test_unknown_named_connection_does_not_fall_back_to_default(self):
        with pytest.raises(ValueError, match="Storage connection .* not found"):
            injector.resolve_storage_credentials("missing_connection_for_stage_test")

    def test_reads_from_storage_connection(self, monkeypatch):
        conn = StorageConnectionConfig(
            name="production",
            type="minio",
            endpoint="http://minio:9000",
            bucket="b",
            access_key="from-config",
            secret_key="from-config-secret",
        )
        monkeypatch.setattr(injector, "get_storage_connection", lambda name: conn)
        assert injector.resolve_storage_credentials("production") == (
            "from-config",
            "from-config-secret",
        )


class TestReferencedSecretGoesThroughTheSameRedaction:
    """NOVA-58: a provider-resolved value is redacted like any other.

    The reference layer changes *where* a credential comes from, not what may
    leave the process. These assertions hold the new source to the existing
    invariant.
    """

    SENTINEL_ACCESS = "AKIA_SECRET_REF_SENTINEL"
    SENTINEL_SECRET = "SECRET_REF_SENTINEL_VALUE"

    def test_resolved_credentials_reach_get_credential_params_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            injector,
            "resolve_storage_credentials",
            lambda storage_connection=None: (self.SENTINEL_ACCESS, self.SENTINEL_SECRET),
        )
        params = injector.get_credential_params("s3")
        assert params["aws.s3.access_key"] == self.SENTINEL_ACCESS
        assert params["aws.s3.secret_key"] == self.SENTINEL_SECRET

    def test_injected_sql_carrying_a_referenced_secret_is_redactable(self):
        from app.common.sql_guard import redact_sql_credentials

        sql = (
            "SELECT * FROM FILES('path'='s3://b/k', 'format'='csv', "
            f"'aws.s3.access_key'='{self.SENTINEL_ACCESS}', "
            f"'aws.s3.secret_key'='{self.SENTINEL_SECRET}')"
        )
        out = redact_sql_credentials(sql)
        assert self.SENTINEL_ACCESS not in out
        assert self.SENTINEL_SECRET not in out

    def test_secret_provider_module_has_no_credential_literal(self):
        import app.storage.secrets as secrets_module

        source = inspect.getsource(secrets_module)
        assert "minioadmin" not in source
        assert "miniopassword" not in source


class TestSecretMasking:
    def test_mask_keeps_only_tail(self):
        masked = mask_secret("sk-supersecretvalue1234")
        assert masked is not None
        assert masked.endswith("1234")
        assert "supersecret" not in masked

    def test_short_secret_fully_masked(self):
        masked = mask_secret("abc")
        assert masked is not None
        assert "abc" not in masked

    def test_none_stays_none(self):
        assert mask_secret(None) is None
        assert mask_secret("") is None

    def test_mask_roundtrip_over_encryption(self):
        """The API-key pipeline stores encrypted and only ever returns a mask."""
        ciphertext = encrypt("sk-live-abcdefghijklmnop")
        assert ciphertext is not None and ciphertext.startswith("enc:")
        plaintext = decrypt(ciphertext)
        assert plaintext is not None
        masked = mask_secret(plaintext)
        assert masked is not None
        assert plaintext not in masked
        assert masked.endswith("mnop")


class TestProviderResponseNeverCarriesPlaintextKey:
    """Acceptance (d): /api/v1/ai/providers must not expose the api_key."""

    def test_provider_schema_has_no_api_key_field(self):
        from app.modules.ai_ml.schemas import AIProviderResponse

        assert "api_key" not in AIProviderResponse.model_fields
        assert "has_api_key" in AIProviderResponse.model_fields
        assert "api_key_masked" in AIProviderResponse.model_fields

    def test_serialized_provider_has_no_plaintext(self):
        from app.modules.ai_ml.schemas import AIProviderResponse

        secret = "sk-live-super-secret-key"
        row = {
            "id": "p1",
            "name": "openai",
            "type": "openai",
            "endpoint": "https://api.example.com/v1",
            "api_key_masked": mask_secret(secret),
            "has_api_key": True,
        }
        payload = AIProviderResponse(**row).model_dump()
        assert secret not in str(payload)
        assert payload["has_api_key"] is True
        assert payload["api_key_masked"] is not None
        assert payload["api_key_masked"].endswith("e-key"[-4:])

    def test_masking_helper_strips_and_flags_the_key(self):
        from app.modules.ai_ml.service import ai_service

        secret = "sk-live-super-secret-key"
        row = {"id": "p1", "api_key": encrypt(secret)}
        masked = ai_service._mask_api_key(row)
        assert "api_key" not in masked
        assert masked["has_api_key"] is True
        assert secret not in str(masked)

    def test_masking_helper_flags_absent_key(self):
        from app.modules.ai_ml.service import ai_service

        masked = ai_service._mask_api_key({"id": "p1", "api_key": None})
        assert masked["has_api_key"] is False
        assert masked["api_key_masked"] is None

    def test_secret_shorter_than_tail_is_not_leaked(self):
        from app.modules.ai_ml.service import ai_service

        masked = ai_service._mask_api_key({"id": "p1", "api_key": encrypt("abc")})
        assert "abc" not in str(masked)
