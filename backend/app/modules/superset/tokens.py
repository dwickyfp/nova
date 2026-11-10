"""BI handles and data tokens, kept in Redis and bound to a Nova session.

Both credentials are derived from a Nova session and die with it. Neither is a
second identity: every check resolves the session again and compares the
principal, the active role and the security-context version captured when the
credential was issued, so a role switch, a logout or an expired session
invalidates them without any extra revocation step.

Redis layout (all keys expire on their own):

* ``nova:bi:handle:<sha256(handle)>`` → session id. The cookie value is only
  stored hashed, so reading Redis does not yield a usable cookie.
* ``nova:bi:token:<token id>`` → hash of the binding plus the Fernet-encrypted
  token. The token is encrypted, not hashed, because the proxy verifies a
  ``mysql_native_password`` response, which needs the token value itself.
* ``nova:bi:tokens:<principal>`` → sorted set of token ids by expiry, bounded
  by ``SUPERSET_MAX_TOKENS_PER_PRINCIPAL``.
* ``nova:bi:bind:<session id>`` → the session's current token id, so a
  dashboard opening twenty connections reuses one token instead of minting
  twenty.
* ``nova:bi:delegated:<id>`` → a delegated session for one background run
  (:mod:`app.modules.superset.delegation`). Its session id is
  ``delegated:<id>``; it has no password, and the proxy reaches the engine
  through the worker account's impersonation of the owner instead.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from app.core.config import settings
from app.core.redis import session_store
from app.modules.access_control import data_tokens
from app.modules.access_control.data_tokens import (
    DELEGATED,
    BiCredentialError,
    BiSession,
    DataToken,
    TokenBinding,
    resolve_session,
    verify_binding,
)

_HANDLE_PREFIX = "nova:bi:handle:"
_DELEGATED_PREFIX = data_tokens.DELEGATED_PREFIX


def _redis():
    redis = session_store._redis
    if redis is None:
        raise BiCredentialError("Session store is not initialized")
    return redis


def _handle_key(handle: str) -> str:
    return _HANDLE_PREFIX + hashlib.sha256(handle.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


async def create_delegated_session(username: str, active_role: str) -> str:
    """Record an authorized delegation for ``SUPERSET_DELEGATION_TTL_SECONDS``.

    Returns its session id (``delegated:<id>``). The caller has already
    authorized the delegation (:func:`app.modules.superset.delegation.authorize`).
    """
    delegated_id = uuid.uuid4().hex
    key = _DELEGATED_PREFIX + delegated_id
    await _redis().hset(
        key, mapping={"username": username, "active_role": active_role, "version": "1"}
    )
    await _redis().expire(key, settings.SUPERSET_DELEGATION_TTL_SECONDS)
    return DELEGATED + delegated_id


async def issue_delegated_handle(username: str, active_role: str) -> str:
    """A BI handle for one background run as ``username`` under ``active_role``."""
    session_id = await create_delegated_session(username, active_role)
    handle = secrets.token_urlsafe(32)
    await _redis().set(
        _handle_key(handle), session_id, ex=settings.SUPERSET_DELEGATION_TTL_SECONDS
    )
    return handle


# ── BI handles (the /bi cookie) ──────────────────────────────────────────


async def issue_handle(session_id: str) -> str:
    """Create a BI handle for ``session_id`` and return its cookie value."""
    await resolve_session(session_id)
    handle = secrets.token_urlsafe(32)
    await _redis().set(_handle_key(handle), session_id, ex=settings.SESSION_TTL_SECONDS)
    return handle


async def revoke_handle(handle: str) -> None:
    await _redis().delete(_handle_key(handle))


async def session_for_handle(handle: str) -> BiSession:
    """Resolve a BI handle to its live session and keep both alive."""
    if not handle:
        raise BiCredentialError("No BI handle")
    redis = _redis()
    key = _handle_key(handle)
    session_id = _text(await redis.get(key))
    if not session_id:
        raise BiCredentialError("BI handle is unknown or expired")
    bi_session = await resolve_session(session_id)
    if not bi_session.delegated:
        await session_store.refresh(session_id)
        await redis.expire(key, settings.SESSION_TTL_SECONDS)
    return bi_session


# ── Data tokens (the proxy password) ─────────────────────────────────────
# Owned by :mod:`app.modules.access_control.data_tokens`; BI uses the ``bi``
# purpose, whose Redis keys are the ``nova:bi:token:*`` family documented above.


async def mint_data_token(bi_session: BiSession) -> DataToken:
    """Return Superset's data token for ``bi_session`` (see ``data_tokens.mint``)."""
    return await data_tokens.mint(
        bi_session,
        purpose=data_tokens.BI,
        ttl=settings.SUPERSET_DATA_TOKEN_TTL_SECONDS,
        max_per_principal=settings.SUPERSET_MAX_TOKENS_PER_PRINCIPAL,
    )


async def match_data_token(
    username: str, scramble: bytes, auth_response: bytes
) -> TokenBinding | None:
    """Return the Superset token binding ``auth_response`` was computed from."""
    return await data_tokens.match(username, scramble, auth_response, purpose=data_tokens.BI)


__all__ = [
    "BiCredentialError",
    "BiSession",
    "DataToken",
    "TokenBinding",
    "issue_handle",
    "match_data_token",
    "mint_data_token",
    "resolve_session",
    "revoke_handle",
    "session_for_handle",
    "verify_binding",
]
