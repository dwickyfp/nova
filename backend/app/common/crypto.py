"""Encryption utilities for sensitive data (API keys, etc).

Uses Fernet symmetric encryption. The key is stored in FERNET_KEY env var.
API keys are encrypted before storing in database and decrypted when read.
This prevents masking issues — encrypted data doesn't match sk-* pattern.
"""

import base64
import logging

from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Get or create Fernet instance."""
    global _fernet
    if _fernet is None:
        key = settings.FERNET_KEY
        if not key:
            # Generate a key if none set (should not happen in production)
            key = Fernet.generate_key().decode()
            logger.warning("FERNET_KEY not set, generated temporary key")
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a string. Returns encrypted string prefixed with 'enc:'.
    
    If input is None or already encrypted (starts with 'enc:'), returns as-is.
    """
    if plaintext is None or plaintext == "":
        return None
    if plaintext.startswith("enc:"):
        return plaintext  # Already encrypted
    try:
        encrypted = _get_fernet().encrypt(plaintext.encode()).decode()
        return f"enc:{encrypted}"
    except Exception as e:
        logger.error("Encryption failed: %s", e)
        return plaintext  # Fallback to plain


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a string. Returns plaintext.
    
    If input is None or not encrypted (no 'enc:' prefix), returns as-is.
    """
    if ciphertext is None or ciphertext == "":
        return None
    if not ciphertext.startswith("enc:"):
        return ciphertext  # Not encrypted, return as-is (plain text)
    try:
        encrypted_part = ciphertext[4:]  # Remove 'enc:' prefix
        return _get_fernet().decrypt(encrypted_part.encode()).decode()
    except Exception as e:
        logger.error("Decryption failed: %s", e)
        return ciphertext  # Return as-is on error
