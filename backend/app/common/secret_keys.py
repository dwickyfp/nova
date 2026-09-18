"""Fail-closed validation for the keys that protect persisted secrets (NOVA-108).

Two process-wide secrets back Nova's credential storage:

* ``SECRET_KEY`` — signs JWT session tokens (``app.core.security``).
* ``FERNET_KEY`` — encrypts StarRocks passwords and stored API keys
  (``app.core.security`` and ``app.common.crypto``).

Both previously had fail-open defaults: an unset ``FERNET_KEY`` silently became
a fresh per-process key (so every restart and every worker saw undecryptable
ciphertext), and ``SECRET_KEY`` fell back to a constant published in this repo
(so anyone could mint a valid token). Neither is acceptable in a deployment, and
a per-process key is not even usable across restart — the safer failure is to
refuse to run rather than appear to work.

This module holds the single validity rule so the three call sites (the JWT
helpers, both Fernet helpers) cannot drift apart.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import settings

#: Values shipped in the repo as examples. A deployment that still has one of
#: these has no secret at all — the value is public.
KNOWN_PLACEHOLDER_SECRET_KEYS = frozenset(
    {
        "change-me-in-production-use-openssl-rand-hex-32",
        "change-me-in-production",
        "change-me-in-local-development",
    }
)

#: Reported by ``validate_secret_key`` / ``validate_fernet_key``; carries no
#: secret value, only the variable name and why it was rejected.
class MissingSecretError(RuntimeError):
    """A required secret is unset or is a known placeholder."""


def validate_secret_key(value: str, *, source: str = "SECRET_KEY") -> str:
    """Return ``value`` if it is a real secret, else raise.

    A blank value, or one equal to a placeholder published in this repo, is
    treated as "not configured": such a key cannot authenticate anything.
    """
    if not value or not value.strip():
        raise MissingSecretError(
            f"{source} is not set. Refusing to start: an unset signing key would "
            "let anyone forge sessions. Generate one with "
            "`openssl rand -hex 32` and set it in the environment."
        )
    if value in KNOWN_PLACEHOLDER_SECRET_KEYS:
        raise MissingSecretError(
            f"{source} is still the repository placeholder. Refusing to start: "
            "the placeholder is public, so any token signed with it is "
            "forgeable. Generate a real value with `openssl rand -hex 32`."
        )
    return value


def validate_fernet_key(value: str, *, source: str = "FERNET_KEY") -> str:
    """Return ``value`` if it is a usable Fernet key, else raise.

    Validates the *shape* rather than merely emptiness: ``Fernet`` accepts a
    malformed-but-non-empty key at construction time with a confusing error, and
    a key generated per process is the exact bug this check exists to prevent.
    """
    if not value or not value.strip():
        raise MissingSecretError(
            f"{source} is not set. Refusing to start: without a fixed key, "
            "every restart and every worker generates its own, and stored "
            "secrets become permanently undecryptable. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`."
        )
    try:
        Fernet(value.encode() if isinstance(value, str) else value)
    except Exception as exc:  # noqa: BLE001 - surfaced as a config error
        raise MissingSecretError(
            f"{source} is not a valid Fernet key ({exc}). Refusing to start; "
            "regenerate it with `python -c \"from cryptography.fernet import "
            "Fernet; print(Fernet.generate_key().decode())\"`."
        ) from exc
    return value


def validate_required_secrets(
    *, secret_key: str, fernet_key: str
) -> None:
    """Validate both process-wide secrets, raising on the first failure.

    Called from application startup so a misconfigured deployment dies at boot
    with a clear message, rather than at the first login (or, worse, silently
    minting forgeable tokens).
    """
    validate_secret_key(secret_key)
    validate_fernet_key(fernet_key)


def guard_process_startup() -> None:
    """Validate the configured secrets for any Nova process entrypoint.

    Every standalone process (the FastAPI lifespan, ``app.worker``,
    ``app.proxy``, ``app.scheduler``) must call this before it opens any
    connection. Centralising the settings read here keeps the four entrypoints
    on one rule: a deployment with a blank or placeholder key refuses to run,
    whichever process starts first, instead of failing later at first use.
    """
    validate_required_secrets(
        secret_key=settings.SECRET_KEY,
        fernet_key=settings.FERNET_KEY,
    )
