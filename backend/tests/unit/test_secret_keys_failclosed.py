"""NOVA-108 — crypto/config must fail closed.

The defects under test:

* ``encrypt()`` returned the plaintext when encryption failed, so an API key
  could be persisted in the clear; ``decrypt()`` returned the ciphertext when
  decryption failed, handing the caller a bogus secret.
* ``FERNET_KEY``/``SECRET_KEY`` were optional: an unset Fernet key became a
  random per-process key (undecryptable across restart/worker) and an unset JWT
  key became a constant published in this repo (forgeable tokens).

These are unit-level: no engine, no Redis, no HTTP.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.common import crypto as crypto_module
from app.common import secret_keys as secret_keys_module
from app.common.crypto import EncryptionError, decrypt, encrypt
from app.common.secret_keys import (
    MissingSecretError,
    validate_fernet_key,
    validate_required_secrets,
    validate_secret_key,
)

VALID_FERNET_KEY = "8f3Q1sVx0m2pR7tY5uW9zB4cD6eF1gH3jK5lM7nO9pQ="


class TestEncryptFailsClosed:
    def test_encrypt_raises_instead_of_returning_plaintext(self, monkeypatch):
        """A cipher that raises must not degrade to the plaintext."""
        monkeypatch.setattr(crypto_module.settings, "FERNET_KEY", VALID_FERNET_KEY)
        monkeypatch.setattr(crypto_module, "_fernet", None)

        class Boom:
            def encrypt(self, _):
                raise RuntimeError("kms unavailable")

        monkeypatch.setattr(crypto_module, "_get_fernet", lambda: Boom())

        with pytest.raises(EncryptionError):
            encrypt("sk-live-must-not-be-stored-in-the-clear")

    def test_encrypt_failure_never_returns_the_secret(self, monkeypatch):
        monkeypatch.setattr(crypto_module, "_get_fernet", _raising_fernet)
        secret = "sk-live-must-not-leak"
        with pytest.raises(EncryptionError) as excinfo:
            encrypt(secret)
        assert secret not in str(excinfo.value)


class TestDecryptFailsClosed:
    def test_decrypt_raises_instead_of_returning_ciphertext(self, monkeypatch):
        monkeypatch.setattr(crypto_module, "_get_fernet", _raising_fernet)
        with pytest.raises(EncryptionError):
            decrypt("enc:not-a-real-token")

    def test_corrupt_ciphertext_raises(self, monkeypatch):
        """A well-formed key + garbage token is a hard error, not a passthrough."""
        monkeypatch.setattr(crypto_module.settings, "FERNET_KEY", VALID_FERNET_KEY)
        monkeypatch.setattr(crypto_module, "_fernet", None)

        with pytest.raises(EncryptionError):
            decrypt("enc:AAAA-not-valid-fernet")

    def test_unprefixed_value_is_still_passed_through(self, monkeypatch):
        """Legacy plaintext (no 'enc:') is not an error; only marked values are."""
        monkeypatch.setattr(crypto_module, "_get_fernet", _raising_fernet)
        assert decrypt("plain-legacy-value") == "plain-legacy-value"


class TestRoundTripStillWorks:
    def test_encrypt_decrypt_round_trip(self, monkeypatch):
        monkeypatch.setattr(crypto_module.settings, "FERNET_KEY", VALID_FERNET_KEY)
        monkeypatch.setattr(crypto_module, "_fernet", None)
        secret = "sk-live-round-trip"
        ciphertext = encrypt(secret)
        assert ciphertext is not None and ciphertext.startswith("enc:")
        assert decrypt(ciphertext) == secret

    def test_none_and_empty_are_unchanged(self):
        assert encrypt(None) is None
        assert encrypt("") is None
        assert decrypt(None) is None
        assert decrypt("") is None


class TestSecretKeyValidation:
    def test_blank_secret_key_is_rejected(self):
        with pytest.raises(MissingSecretError):
            validate_secret_key("")
        with pytest.raises(MissingSecretError):
            validate_secret_key("   ")

    @pytest.mark.parametrize(
        "placeholder",
        [
            "change-me-in-production-use-openssl-rand-hex-32",
            "change-me-in-production",
            "change-me-in-local-development",
        ],
    )
    def test_known_placeholder_is_rejected(self, placeholder):
        with pytest.raises(MissingSecretError):
            validate_secret_key(placeholder)

    def test_real_secret_passes(self):
        assert validate_secret_key("a-real-random-secret") == "a-real-random-secret"


class TestFernetKeyValidation:
    def test_blank_fernet_key_is_rejected(self):
        with pytest.raises(MissingSecretError):
            validate_fernet_key("")

    def test_malformed_fernet_key_is_rejected(self):
        """Non-empty but not a Fernet key: must fail here, not at first use."""
        with pytest.raises(MissingSecretError):
            validate_fernet_key("not-a-fernet-key")

    def test_generated_key_passes(self):
        key = Fernet.generate_key().decode()
        assert validate_fernet_key(key) == key


class TestValidateRequiredSecrets:
    def test_missing_secret_key_raises(self):
        with pytest.raises(MissingSecretError) as excinfo:
            validate_required_secrets(secret_key="", fernet_key=VALID_FERNET_KEY)
        assert "SECRET_KEY" in str(excinfo.value)

    def test_missing_fernet_key_raises(self):
        with pytest.raises(MissingSecretError) as excinfo:
            validate_required_secrets(secret_key="real-secret", fernet_key="")
        assert "FERNET_KEY" in str(excinfo.value)

    def test_both_present_passes(self):
        validate_required_secrets(
            secret_key="real-secret", fernet_key=VALID_FERNET_KEY
        )

    def test_error_message_carries_no_secret_value(self):
        secret = "super-secret-signing-value"
        with pytest.raises(MissingSecretError) as excinfo:
            validate_required_secrets(secret_key=secret, fernet_key="")
        assert secret not in str(excinfo.value)


class TestTwoProcessesShareTheKey:
    """Acceptance 3: same env → two independent instances interoperate."""

    def test_two_crypto_instances_decrypt_each_other(self, monkeypatch):
        monkeypatch.setattr(crypto_module.settings, "FERNET_KEY", VALID_FERNET_KEY)
        monkeypatch.setattr(crypto_module, "_fernet", None)
        ciphertext = encrypt("cross-process-secret")

        # Simulate a second process: fresh module cache, same configured key.
        monkeypatch.setattr(crypto_module, "_fernet", None)
        assert decrypt(ciphertext) == "cross-process-secret"


def _raising_fernet():
    class Boom:
        def encrypt(self, _):
            raise RuntimeError("boom")

        def decrypt(self, _):
            raise RuntimeError("boom")

    return Boom()


class TestConfigHasNoUsableDefaults:
    """The shipped defaults must not be a working signing key (NOVA-108).

    These assertions are about what the code **ships**, so the check must not be
    influenced by a developer's ``backend/.env`` or an exported variable. A plain
    ``Settings()`` reads both (the class declares ``env_file=".env"``), which
    made the test pass in CI and fail on any machine with a real key configured
    — the local-config leak NOVA-108 is about, one level up. ``_DefaultsOnly``
    drops every external source and leaves only the field defaults, so the test
    measures the code and nothing else.
    """

    @staticmethod
    def _defaults_only():
        from pydantic_settings import BaseSettings

        from app.core.config import Settings

        class _DefaultsOnly(Settings):
            model_config = BaseSettings.model_config

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                # No init, no process env, no .env, no secrets dir: only the
                # declared field defaults remain.
                return ()

        return _DefaultsOnly()

    def test_default_secret_key_is_not_a_published_constant(self):
        defaults = self._defaults_only()
        assert defaults.SECRET_KEY == ""
        assert defaults.FERNET_KEY == ""

    def test_placeholder_set_is_shared_by_validation(self):
        defaults = self._defaults_only()
        assert defaults.SECRET_KEY not in secret_keys_module.KNOWN_PLACEHOLDER_SECRET_KEYS

