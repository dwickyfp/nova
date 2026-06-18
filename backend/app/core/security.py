"""JWT token management and credential encryption."""

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from jose import JWTError, jwt

from app.core.config import settings

# --- JWT ---

ALGORITHM = "HS256"


def create_access_token(username: str, session_id: str) -> str:
    """Create a JWT token with username and session ID."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "sid": session_id, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Returns payload dict.

    Raises JWTError if token is invalid or expired.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


# --- Fernet Encryption (for DB passwords in Redis sessions) ---

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazy-init Fernet instance. Generates key if not configured."""
    global _fernet
    if _fernet is None:
        key = settings.FERNET_KEY
        if not key:
            key = Fernet.generate_key().decode()
            # In production, this should be set in .env
            # For dev, we generate a new one each startup
        _fernet = Fernet(key if isinstance(key, bytes) else key.encode())
    return _fernet


def encrypt_password(password: str) -> str:
    """Encrypt a password for storage in Redis session."""
    return _get_fernet().encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt a password from Redis session."""
    return _get_fernet().decrypt(encrypted.encode()).decode()
