"""The Tavily API key that Studio's ``web_search`` tool uses.

The key is a system credential set by an administrator on the AI provider
settings page. It is stored Fernet-encrypted in the system settings row of
the preference metadata (the same row family as the default LLM), with
the same ``encrypt``/``decrypt`` helpers that protect provider API keys. Callers
outside the tool only ever see whether a key is set and the
key's public type prefix (``tvly-dev-`` or ``tvly-``), which is taken from a
fixed list so no secret character is ever kept in plain text.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ConfigDict

from app.common.crypto import EncryptionError, decrypt, encrypt
from app.common.preference_store import SYSTEM_OWNER, read_value, write_values
from app.modules.access_control.security_context import SecurityContext

logger = logging.getLogger(__name__)

SYSTEM_USER = SYSTEM_OWNER
TAVILY_KEY_PREF = "tavily_api_key"
#: Tavily's published key-type prefixes, longest first. The hint is one of
#: these exact strings, never characters copied from the secret part.
_KEY_PREFIXES = ("tvly-dev-", "tvly-prod-", "tvly-")


class WebSearchStatus(BaseModel):
    """What the settings page may know: whether a key is stored, never the key."""

    provider: str = "tavily"
    configured: bool
    #: The key's public type prefix for a masked display, or ``None``.
    key_hint: str | None = None


class TavilyKeyRequest(BaseModel):
    """A write-only key update. The value is never echoed back.

    The schema has no length rules on purpose: a Pydantic validation error
    would return the submitted value in its 422 body. ``clean_tavily_key``
    checks the shape and fails with a fixed message instead.
    """

    model_config = ConfigDict(extra="forbid")

    api_key: str


def clean_tavily_key(value: str) -> str | None:
    """The trimmed key, or ``None`` when it cannot be a Tavily key."""
    value = value.strip()
    if not 8 <= len(value) <= 256 or any(ch.isspace() for ch in value):
        return None
    return value


def key_hint(api_key: str) -> str | None:
    """The key's published type prefix, or ``None`` for any other shape."""
    return next((prefix for prefix in _KEY_PREFIXES if api_key.startswith(prefix)), None)


async def _stored_value(actor: SecurityContext) -> tuple[str, str | None] | None:
    """The stored ciphertext and its prefix hint, or ``None`` when unset.

    RLS lets only the web and agent logins read this row; the raw record never
    leaves this module.
    """
    value = await read_value(actor, SYSTEM_USER, TAVILY_KEY_PREF)
    if not isinstance(value, str) or not value:
        return None
    try:
        record = json.loads(value)
    except ValueError:
        return value, None  # a bare value, without a hint
    if not isinstance(record, dict) or not isinstance(record.get("api_key"), str):
        return None
    hint = record.get("hint")
    return record["api_key"], hint if hint in _KEY_PREFIXES else None


async def web_search_status(actor: SecurityContext) -> WebSearchStatus:
    stored = await _stored_value(actor)
    if stored is None:
        return WebSearchStatus(configured=False)
    return WebSearchStatus(configured=True, key_hint=stored[1])


async def tavily_api_key(actor: SecurityContext) -> str | None:
    """The decrypted key, for the ``web_search`` tool only.

    Returns ``None`` when no key is stored or the stored value cannot be
    decrypted (for example after a ``FERNET_KEY`` rotation); the tool then
    reports that web search is not configured instead of using a bad secret.
    """
    stored = await _stored_value(actor)
    if stored is None or not stored[0].startswith("enc:"):
        return None
    try:
        return decrypt(stored[0])
    except EncryptionError:
        logger.warning("The stored Tavily API key cannot be decrypted")
        return None


async def save_tavily_api_key(api_key: str, actor: SecurityContext) -> WebSearchStatus:
    encrypted = encrypt(api_key)  # raises EncryptionError: never store plaintext
    hint = key_hint(api_key)
    await write_values(
        actor,
        SYSTEM_USER,
        {TAVILY_KEY_PREF: json.dumps({"api_key": encrypted, "hint": hint})},
    )
    return WebSearchStatus(configured=True, key_hint=hint)


async def clear_tavily_api_key(actor: SecurityContext) -> WebSearchStatus:
    """Delete the key; the row keeps a revisioned tombstone without a value."""
    await write_values(actor, SYSTEM_USER, {TAVILY_KEY_PREF: None})
    return WebSearchStatus(configured=False)
