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
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

from cryptography.fernet import InvalidToken

from app.core.config import settings
from app.core.redis import session_store
from app.core.security import decrypt_password, encrypt_password
from app.proxy.protocol import scramble_native_password

_HANDLE_PREFIX = "nova:bi:handle:"
_TOKEN_PREFIX = "nova:bi:token:"
_INDEX_PREFIX = "nova:bi:tokens:"
_BIND_PREFIX = "nova:bi:bind:"
_DELEGATED_PREFIX = "nova:bi:delegated:"
DELEGATED = "delegated:"


class BiCredentialError(Exception):
    """A BI handle or data token no longer maps to a usable Nova session."""


@dataclass(frozen=True)
class BiSession:
    """The Nova session a BI credential resolves to, as of this check."""

    session_id: str
    username: str
    active_role: str
    security_context_version: int
    encrypted_password: str
    #: A background run's session: the owner under their default role, reached
    #: through the worker account's impersonation rather than a password.
    delegated: bool = False


@dataclass(frozen=True)
class DataToken:
    """A data token as handed to Superset's connection mutator."""

    username: str
    token: str
    expires_in: int


@dataclass(frozen=True)
class TokenBinding:
    """What a verified data token is bound to, for the proxy."""

    token_id: str
    session_id: str
    username: str
    active_role: str
    security_context_version: int


def _redis():
    redis = session_store._redis
    if redis is None:
        raise BiCredentialError("Session store is not initialized")
    return redis


def _handle_key(handle: str) -> str:
    return _HANDLE_PREFIX + hashlib.sha256(handle.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


async def resolve_session(session_id: str) -> BiSession:
    """Return the session's current security state, or raise.

    A session without an explicit active role, or whose stored password no
    longer decrypts (key rotation), is not usable for BI.
    """
    if session_id.startswith(DELEGATED):
        return await _delegated_session(session_id)
    session = await session_store.get(session_id)
    if not session:
        raise BiCredentialError("Nova session has ended")
    username = session.get("username") or ""
    active_role = session.get("active_role") or ""
    if not username or not active_role:
        raise BiCredentialError("Nova session has no active role")
    encrypted_password = session.get("encrypted_password") or ""
    try:
        decrypt_password(encrypted_password)
    except (InvalidToken, TypeError, ValueError) as exc:
        raise BiCredentialError("Nova session is no longer readable") from exc
    return BiSession(
        session_id=session_id,
        username=username,
        active_role=active_role,
        security_context_version=int(session.get("security_context_version") or 1),
        encrypted_password=encrypted_password,
    )


async def _delegated_session(session_id: str) -> BiSession:
    stored = await _redis().hgetall(_DELEGATED_PREFIX + session_id[len(DELEGATED) :])
    fields = {_text(k): _text(v) for k, v in stored.items()}
    if not fields.get("username") or not fields.get("active_role"):
        raise BiCredentialError("The delegated BI session has ended")
    return BiSession(
        session_id=session_id,
        username=fields["username"],
        active_role=fields["active_role"],
        security_context_version=int(fields.get("version") or 1),
        encrypted_password="",
        delegated=True,
    )


async def issue_delegated_handle(username: str, active_role: str) -> str:
    """A BI handle for one background run as ``username`` under ``active_role``.

    The caller has already authorized the delegation; this only records it,
    for ``SUPERSET_DELEGATION_TTL_SECONDS``.
    """
    ttl = settings.SUPERSET_DELEGATION_TTL_SECONDS
    delegated_id = uuid.uuid4().hex
    key = _DELEGATED_PREFIX + delegated_id
    await _redis().hset(
        key, mapping={"username": username, "active_role": active_role, "version": "1"}
    )
    await _redis().expire(key, ttl)
    handle = secrets.token_urlsafe(32)
    await _redis().set(_handle_key(handle), DELEGATED + delegated_id, ex=ttl)
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


async def mint_data_token(bi_session: BiSession) -> DataToken:
    """Return a data token for ``bi_session``, reusing a fresh one if it exists.

    A token is reused only while it is bound to the same role and context
    version and has more than half its lifetime left, so a connection opened
    with it cannot outlive the binding by more than one TTL.
    """
    redis = _redis()
    ttl = settings.SUPERSET_DATA_TOKEN_TTL_SECONDS
    now = time.time()

    current_id = _text(await redis.get(_BIND_PREFIX + bi_session.session_id))
    if current_id:
        stored = await redis.hgetall(_TOKEN_PREFIX + current_id)
        fields = {_text(k): _text(v) for k, v in stored.items()}
        remaining = float(fields.get("expires_at") or 0) - now
        if (
            fields
            and fields.get("active_role") == bi_session.active_role
            and int(fields.get("version") or 0) == bi_session.security_context_version
            and remaining > ttl / 2
        ):
            try:
                token = decrypt_password(fields["token"])
            except (InvalidToken, KeyError, TypeError, ValueError):
                token = ""
            if token:
                return DataToken(bi_session.username, token, int(remaining))

    token = secrets.token_urlsafe(32)
    token_id = uuid.uuid4().hex
    expires_at = now + ttl
    index = _INDEX_PREFIX + bi_session.username
    await redis.hset(
        _TOKEN_PREFIX + token_id,
        mapping={
            "session_id": bi_session.session_id,
            "username": bi_session.username,
            "active_role": bi_session.active_role,
            "version": str(bi_session.security_context_version),
            "expires_at": f"{expires_at:.3f}",
            "token": encrypt_password(token),
        },
    )
    await redis.expire(_TOKEN_PREFIX + token_id, ttl)
    await redis.set(_BIND_PREFIX + bi_session.session_id, token_id, ex=ttl)
    await redis.zadd(index, {token_id: expires_at})
    await redis.zremrangebyscore(index, "-inf", now)
    excess = await redis.zcard(index) - settings.SUPERSET_MAX_TOKENS_PER_PRINCIPAL
    if excess > 0:
        for evicted, _score in await redis.zpopmin(index, excess):
            await redis.delete(_TOKEN_PREFIX + _text(evicted))
    await redis.expire(index, ttl)
    return DataToken(bi_session.username, token, ttl)


async def match_data_token(
    username: str, scramble: bytes, auth_response: bytes
) -> TokenBinding | None:
    """Return the binding whose token produced ``auth_response``, if any.

    ``auth_response`` is a ``mysql_native_password`` response to ``scramble``;
    it is recomputed from each live token of ``username`` and compared in
    constant time. Only bounded, unexpired tokens are tried. The binding is
    returned as stored; :func:`verify_binding` decides whether it still holds.
    """
    if len(auth_response) != 20 or not username:
        return None
    redis = _redis()
    index = _INDEX_PREFIX + username
    now = time.time()
    for raw_id in await redis.zrangebyscore(index, now, "+inf"):
        token_id = _text(raw_id)
        stored = await redis.hgetall(_TOKEN_PREFIX + token_id)
        if not stored:
            await redis.zrem(index, token_id)
            continue
        fields = {_text(k): _text(v) for k, v in stored.items()}
        if fields.get("username") != username:
            continue
        try:
            token = decrypt_password(fields["token"])
        except (InvalidToken, KeyError, TypeError, ValueError):
            continue
        expected = scramble_native_password(token, scramble)
        if hmac.compare_digest(expected, auth_response):
            return TokenBinding(
                token_id=token_id,
                session_id=fields.get("session_id", ""),
                username=username,
                active_role=fields.get("active_role", ""),
                security_context_version=int(fields.get("version") or 0),
            )
    return None


async def verify_binding(binding: TokenBinding) -> BiSession:
    """Return the bound session if the binding still holds, else raise.

    Holds only while the session exists for the same principal, with the same
    active role and the same security-context version as at issue time.
    """
    bi_session = await resolve_session(binding.session_id)
    if (
        bi_session.username != binding.username
        or bi_session.active_role != binding.active_role
        or bi_session.security_context_version != binding.security_context_version
    ):
        raise BiCredentialError("Nova session changed since this connection was opened")
    return bi_session


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
