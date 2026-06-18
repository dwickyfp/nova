"""Unit tests for security — JWT + Fernet encryption. No engine needed."""

import pytest
from jose import JWTError

from app.core.security import (
    create_access_token,
    decode_token,
    decrypt_password,
    encrypt_password,
)


class TestJWT:
    def test_create_and_decode_token(self):
        token = create_access_token("testuser", "session-123")
        payload = decode_token(token)
        assert payload["sub"] == "testuser"
        assert payload["sid"] == "session-123"
        assert "exp" in payload

    def test_decode_invalid_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("invalid.token.here")

    def test_different_users_different_tokens(self):
        token1 = create_access_token("user1", "s1")
        token2 = create_access_token("user2", "s2")
        assert token1 != token2
        assert decode_token(token1)["sub"] == "user1"
        assert decode_token(token2)["sub"] == "user2"


class TestFernetEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        password = "my-secret-db-password-123!"
        encrypted = encrypt_password(password)
        assert encrypted != password
        decrypted = decrypt_password(encrypted)
        assert decrypted == password

    def test_different_passwords_different_ciphertext(self):
        enc1 = encrypt_password("password1")
        enc2 = encrypt_password("password2")
        assert enc1 != enc2

    def test_empty_password_roundtrip(self):
        encrypted = encrypt_password("")
        assert decrypt_password(encrypted) == ""

    def test_special_characters_roundtrip(self):
        password = "p@$$w0rd!#%^&*()_+-=[]{}|;':\",./<>?"
        encrypted = encrypt_password(password)
        assert decrypt_password(encrypted) == password
