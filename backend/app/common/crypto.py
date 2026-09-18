"""Encryption utilities for sensitive data (API keys, etc).

Uses Fernet symmetric encryption. The key is stored in FERNET_KEY env var.
API keys are encrypted before storing in database and decrypted when read.
This prevents masking issues — encrypted data doesn't match sk-* pattern.

Both directions fail closed (NOVA-108): a failure to encrypt raises instead of
silently returning the plaintext, and a failure to decrypt raises instead of
silently returning the ciphertext as if it were the secret.
"""

import logging

from cryptography.fernet import Fernet

from app.common.secret_keys import validate_fernet_key
from app.core.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


class EncryptionError(RuntimeError):
    """A value could not be encrypted or decrypted; do not persist it."""


def _build_fernet(key: str) -> Fernet:
    """Validate the configured key and construct its Fernet.

    Split out so a test can exercise the key rules without touching the
    process-wide cache below.
    """
    return Fernet(validate_fernet_key(key).encode())


def _get_fernet() -> Fernet:
    """Get or create the Fernet instance for the configured key."""
    global _fernet
    if _fernet is None:
        _fernet = _build_fernet(settings.FERNET_KEY)
    return _fernet


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string. Returns encrypted string prefixed with 'enc:'.

    If input is None or already encrypted (starts with 'enc:'), returns as-is.
    Raises ``EncryptionError`` if encryption fails: returning the plaintext
    would persist an API key in the clear, which is the one outcome this
    function exists to prevent.
    """
    if plaintext is None or plaintext == "":
        return None
    if plaintext.startswith("enc:"):
        return plaintext  # Already encrypted
    try:
        encrypted = _get_fernet().encrypt(plaintext.encode()).decode()
    except Exception as e:
        logger.error("Encryption failed: %s", e)
        raise EncryptionError("Encryption failed; refusing to store plaintext") from e
    return f"enc:{encrypted}"


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a string. Returns plaintext.

    If input is None or not encrypted (no 'enc:' prefix), returns as-is.
    Raises ``EncryptionError`` if a value that *is* marked encrypted cannot be
    decrypted: returning the ciphertext would hand the caller a bogus secret
    (a bad password is better than a silent wrong one).
    """
    if ciphertext is None or ciphertext == "":
        return None
    if not ciphertext.startswith("enc:"):
        return ciphertext  # Not encrypted, return as-is (plain text)
    encrypted_part = ciphertext[4:]  # Remove 'enc:' prefix
    try:
        return _get_fernet().decrypt(encrypted_part.encode()).decode()
    except EncryptionError:
        # The key itself is misconfigured (raised by _get_fernet); re-raise as-is
        # rather than masking a config error as a data error.
        raise
    except Exception as e:
        logger.error("Decryption failed: %s", e)
        raise EncryptionError("Decryption failed; the value cannot be read") from e


def mask_secret(plaintext: str | None, visible: int = 4) -> str | None:
    """Mask a secret for display, keeping only the last ``visible`` characters.

    Values shorter than the visible window are fully masked. Returns None when
    there is nothing to mask.
    """
    if not plaintext:
        return None
    tail = plaintext[-visible:] if len(plaintext) > visible else ""
    return f"{'•' * 4}{tail}" if tail else "•" * 4

