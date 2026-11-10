"""Session-bound data tokens: short-lived MySQL proxy passwords for a Nova session.

A data token lets a trusted client (Superset, a notebook runtime) open MySQL
proxy connections *as a signed-in user* without ever holding that user's
password. It is not a second identity: every check resolves the Nova session
again and compares the principal, the active role and the security-context
version captured when the token was issued, so a role switch, a logout or an
expired session invalidates it without any extra revocation step.

Tokens are scoped by **purpose**. Each purpose has its own Redis namespace,
its own cap and its own peer networks (checked by the proxy), so a notebook
token is never accepted as a Superset token or the other way round:

* ``bi`` — Superset's per-session token (``app.modules.superset.tokens``);
* ``notebook`` — one notebook runtime's token. It is bound to the runtime as
  well: it stops working when the runtime stops, and the proxy runs every
  statement on its connections read-only.

Redis layout per purpose (all keys expire on their own):

* ``<prefix>token:<token id>`` → hash of the binding plus the Fernet-encrypted
  token. The token is encrypted, not hashed, because the proxy verifies a
  ``mysql_native_password`` response, which needs the token value itself.
* ``<prefix>tokens:<principal>`` → sorted set of token ids by expiry, bounded.
* ``<prefix>bind:<subject>`` → the subject's current token id (the Nova session
  for BI, the runtime for a notebook), so many connections reuse one token.
"""

from __future__ import annotations

import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

from cryptography.fernet import InvalidToken

from app.core.redis import session_store
from app.core.security import decrypt_password, encrypt_password

#: A delegated (background) BI session: owner + role, no password. See
#: :mod:`app.modules.superset.delegation`.
DELEGATED_PREFIX = "nova:bi:delegated:"
DELEGATED = "delegated:"


class BiCredentialError(Exception):
    """A data token or handle no longer maps to a usable Nova session."""


@dataclass(frozen=True)
class TokenPurpose:
    name: str
    #: Redis key prefix of this purpose, e.g. ``nova:bi:``.
    prefix: str
    #: When set, the token holds only while ``<subject_key_prefix><subject>``
    #: exists (the notebook runtime's live state).
    subject_key_prefix: str | None = None

    @property
    def token_prefix(self) -> str:
        return f"{self.prefix}token:"

    @property
    def index_prefix(self) -> str:
        return f"{self.prefix}tokens:"

    @property
    def bind_prefix(self) -> str:
        return f"{self.prefix}bind:"


BI = TokenPurpose(name="bi", prefix="nova:bi:")
NOTEBOOK = TokenPurpose(name="notebook", prefix="nova:nb:", subject_key_prefix="nova:nb:rt:")
PURPOSES = {BI.name: BI, NOTEBOOK.name: NOTEBOOK}


@dataclass(frozen=True)
class BiSession:
    """The Nova session a credential resolves to, as of this check."""

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
    """A data token as handed to the client that will connect with it."""

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
    purpose: str = BI.name
    #: The notebook runtime a notebook token belongs to ("" for BI).
    subject: str = ""

    @property
    def notebook(self) -> bool:
        return self.purpose == NOTEBOOK.name


def _redis():
    redis = session_store._redis
    if redis is None:
        raise BiCredentialError("Session store is not initialized")
    return redis


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


async def resolve_session(session_id: str) -> BiSession:
    """Return the session's current security state, or raise.

    A session without an explicit active role, or whose stored password no
    longer decrypts (key rotation), is not usable.
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
    stored = await _redis().hgetall(DELEGATED_PREFIX + session_id[len(DELEGATED) :])
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


async def mint(
    session: BiSession,
    *,
    purpose: TokenPurpose,
    ttl: int,
    max_per_principal: int,
    subject: str = "",
) -> DataToken:
    """Return a token for ``session`` (and ``subject``), reusing a fresh one.

    A token is reused only while it is bound to the same role and context
    version and has more than half its lifetime left, so a connection opened
    with it cannot outlive the binding by more than one TTL.
    """
    redis = _redis()
    now = time.time()
    bind_key = purpose.bind_prefix + (subject or session.session_id)

    current_id = _text(await redis.get(bind_key))
    if current_id:
        stored = await redis.hgetall(purpose.token_prefix + current_id)
        fields = {_text(k): _text(v) for k, v in stored.items()}
        remaining = float(fields.get("expires_at") or 0) - now
        if (
            fields
            and fields.get("session_id") == session.session_id
            and fields.get("active_role") == session.active_role
            and int(fields.get("version") or 0) == session.security_context_version
            and remaining > ttl / 2
        ):
            try:
                token = decrypt_password(fields["token"])
            except (InvalidToken, KeyError, TypeError, ValueError):
                token = ""
            if token:
                return DataToken(session.username, token, int(remaining))

    token = secrets.token_urlsafe(32)
    token_id = uuid.uuid4().hex
    expires_at = now + ttl
    index = purpose.index_prefix + session.username
    mapping = {
        "session_id": session.session_id,
        "username": session.username,
        "active_role": session.active_role,
        "version": str(session.security_context_version),
        "expires_at": f"{expires_at:.3f}",
        "token": encrypt_password(token),
    }
    if subject:
        mapping["subject"] = subject
    await redis.hset(purpose.token_prefix + token_id, mapping=mapping)
    await redis.expire(purpose.token_prefix + token_id, ttl)
    await redis.set(bind_key, token_id, ex=ttl)
    await redis.zadd(index, {token_id: expires_at})
    await redis.zremrangebyscore(index, "-inf", now)
    excess = await redis.zcard(index) - max_per_principal
    if excess > 0:
        for evicted, _score in await redis.zpopmin(index, excess):
            await redis.delete(purpose.token_prefix + _text(evicted))
    await redis.expire(index, ttl)
    return DataToken(session.username, token, ttl)


async def revoke_subject(purpose: TokenPurpose, subject: str) -> None:
    """Forget the subject's current token (the runtime stopped)."""
    redis = _redis()
    bind_key = purpose.bind_prefix + subject
    token_id = _text(await redis.get(bind_key))
    if token_id:
        await redis.delete(purpose.token_prefix + token_id)
    await redis.delete(bind_key)


async def match(
    username: str, scramble: bytes, auth_response: bytes, *, purpose: TokenPurpose
) -> TokenBinding | None:
    """Return the binding whose token produced ``auth_response``, if any.

    ``auth_response`` is a ``mysql_native_password`` response to ``scramble``;
    it is recomputed from each live token of ``username`` for this purpose and
    compared in constant time. Only bounded, unexpired tokens are tried. The
    binding is returned as stored; :func:`verify_binding` decides whether it
    still holds.
    """
    # Imported here: ``app.proxy`` imports the proxy server, which imports this
    # module through the Superset token wrapper.
    from app.proxy.protocol import scramble_native_password

    if len(auth_response) != 20 or not username:
        return None
    redis = _redis()
    index = purpose.index_prefix + username
    now = time.time()
    for raw_id in await redis.zrangebyscore(index, now, "+inf"):
        token_id = _text(raw_id)
        stored = await redis.hgetall(purpose.token_prefix + token_id)
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
                purpose=purpose.name,
                subject=fields.get("subject", ""),
            )
    return None


async def verify_binding(binding: TokenBinding) -> BiSession:
    """Return the bound session if the binding still holds, else raise.

    Holds only while the session exists for the same principal, with the same
    active role and the same security-context version as at issue time, and —
    for a purpose bound to a subject — while that subject is still live.
    """
    purpose = PURPOSES.get(binding.purpose)
    if purpose is None:
        raise BiCredentialError("Unknown data token purpose")
    if purpose.subject_key_prefix is not None and not (
        binding.subject
        and await _redis().exists(purpose.subject_key_prefix + binding.subject)
    ):
        raise BiCredentialError("The notebook runtime this connection belonged to has stopped")
    session = await resolve_session(binding.session_id)
    if (
        session.username != binding.username
        or session.active_role != binding.active_role
        or session.security_context_version != binding.security_context_version
    ):
        raise BiCredentialError("Nova session changed since this connection was opened")
    return session


__all__ = [
    "BI",
    "DELEGATED",
    "DELEGATED_PREFIX",
    "NOTEBOOK",
    "PURPOSES",
    "BiCredentialError",
    "BiSession",
    "DataToken",
    "TokenBinding",
    "TokenPurpose",
    "match",
    "mint",
    "resolve_session",
    "revoke_subject",
    "verify_binding",
]
