"""JWT token management and credential encryption.

The signing key and the Fernet key are both required (NOVA-108). Neither is
generated on the fly or defaulted to a published constant: an unset key is a
misconfiguration that must fail loudly at startup, not a surprise that
invalidates every session on the next restart.
"""

from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from jose import jwt

from app.common.secret_keys import MissingSecretError, validate_fernet_key
from app.core.config import settings

# --- JWT ---

ALGORITHM = "HS256"


def create_access_token(username: str, session_id: str) -> str:
    """Create a JWT token with username and session ID."""
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "sid": session_id, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Returns payload dict.

    Raises JWTError if token is invalid or expired.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


#: How long after its expiry a token may still be exchanged for a new one.
#: The live Redis session is what actually decides; this only bounds replay
#: of a very old token.
RENEW_GRACE = timedelta(hours=12)


def decode_renewable_token(token: str) -> dict:
    """Decode a correctly signed token that may have expired, within the grace.

    Raises JWTError for a bad signature, a malformed token, or one that expired
    longer ago than :data:`RENEW_GRACE`.
    """
    from jose import JWTError

    payload = jwt.decode(
        token, settings.SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False}
    )
    expired = datetime.fromtimestamp(float(payload.get("exp") or 0), UTC)
    if expired + RENEW_GRACE < datetime.now(UTC) or not payload.get("sid"):
        raise JWTError("The token is too old to renew")
    return payload


# --- Fernet Encryption (for DB passwords in Redis sessions) ---

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazy-init Fernet instance from the required ``FERNET_KEY``."""
    global _fernet
    if _fernet is None:
        _fernet = Fernet(validate_fernet_key(settings.FERNET_KEY).encode())
    return _fernet


def encrypt_password(password: str) -> str:
    """Encrypt a password for storage in Redis session."""
    return _get_fernet().encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt a password from Redis session."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


__all__ = [
    "ALGORITHM",
    "MissingSecretError",
    "create_access_token",
    "decode_token",
    "decrypt_password",
    "encrypt_password",
]
