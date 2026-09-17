"""Owner-credential resolution for delegate-first execution.

The worker runs ``SUBMIT TASK`` on the **task owner's** StarRocks connection so
the engine enforces RBAC (design D9.4). That needs the owner's password, which
exists nowhere in Nova's durable state — the Credential-Invisible invariant
forbids storing it in ``NOVA_SYSTEM``.

The rule this module enforces: a credential is resolved **at execution time,
in memory, and discarded when the connection closes**. It is never written to
``CONFIG_TASK*``, the Redis stream, a log line, or an error message. Every
implementation returns the password to a caller that is about to open a
connection with it — nothing caches it.

``SessionCredentialProvider`` reads the password from the existing session path:
an authenticated user's Fernet-encrypted password in the Redis session store.
Nova has no reverse index from username to session id, so the provider scans the
live session keys and matches on the ``username`` field. A task whose owner has
no live session cannot be executed delegate-first and is reported as
:class:`CredentialUnavailable` — never silently downgraded to a root connection.
"""

from __future__ import annotations

import logging
from typing import Protocol

import redis.asyncio as aioredis

from app.core.redis import SESSION_PREFIX
from app.core.security import decrypt_password

logger = logging.getLogger(__name__)


class CredentialUnavailable(RuntimeError):
    """Raised when no credential can be obtained for the task owner.

    The worker must not fall back to a privileged connection: the whole point of
    delegate-first is that an unauthenticated owner's task cannot run.
    """


class OwnerCredentialProvider(Protocol):
    """Resolves the owning user's StarRocks password at execution time."""

    async def password_for(self, username: str) -> str:
        """Return the plaintext password for ``username``, or raise."""
        ...


class SessionCredentialProvider:
    """Reads the owner's credential from the live Redis session store.

    Only the encrypted blob is ever held between calls; the plaintext lives for
    the duration of one ``password_for`` call and is handed straight to
    ``db.user_conn``.
    """

    def __init__(self, client: aioredis.Redis) -> None:
        self._client = client

    async def password_for(self, username: str) -> str:
        if not username:
            raise CredentialUnavailable("task owner is unknown")

        async for key in self._client.scan_iter(match=f"{SESSION_PREFIX}*"):
            session = await self._client.hgetall(key)
            if not session:
                continue
            if _as_str(session.get("username")) != username:
                continue
            encrypted = _as_str(session.get("encrypted_password"))
            if not encrypted:
                continue
            try:
                return decrypt_password(encrypted)
            except Exception as exc:  # noqa: BLE001 - surfaced as unavailable
                # Never include the blob in the message: it is key material.
                raise CredentialUnavailable(
                    f"stored credential for {username!r} could not be decrypted"
                ) from exc

        raise CredentialUnavailable(
            f"no active session for task owner {username!r}; "
            "delegate-first execution requires the owner to be signed in"
        )


def _as_str(value: object) -> str:
    """Every session field is a string; Redis may hand back bytes."""
    if value is None:
        return ""
    return value.decode() if isinstance(value, bytes) else str(value)


class StaticCredentialProvider:
    """In-memory provider for tests and single-tenant deployments.
    The mapping is passed in by the process that already holds the secrets; it
    is never persisted. Missing users raise, matching the session provider.
    """

    def __init__(self, passwords: dict[str, str]) -> None:
        self._passwords = dict(passwords)

    async def password_for(self, username: str) -> str:
        try:
            return self._passwords[username]
        except KeyError as exc:
            raise CredentialUnavailable(f"no credential for {username!r}") from exc
