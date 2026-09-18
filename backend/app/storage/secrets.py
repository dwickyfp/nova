"""Secret-reference resolution for storage connections.

A storage connection in ``nova.yaml`` may name a credential in an external
secret store instead of carrying the value inline::

    storage:
      connections:
        aws_prod:
          type: s3
          bucket: my-bucket
          secret_ref: arn:aws:secretsmanager:us-east-1:123456789012:secret:nova/s3

Nova stores the *reference* — never the value. ``resolve_secret_reference``
turns a reference into an ``(access_key, secret_key)`` pair at the moment a
FILES() statement or an S3 client is built.

Design rules (NOVA-58):

* **Reference-only.** Nothing in this module persists a secret value. The
  reference is the only durable artefact.
* **Fail-closed.** When a reference is configured and the provider cannot
  resolve it — error, timeout, malformed payload — resolution raises
  ``SecretResolutionError``. It never falls back to the inline ``nova.yaml``
  credentials for that connection: a connection that explicitly opted into a
  secret reference must not silently authenticate with a different principal.
* **No stale cache.** An in-memory TTL cache shortens repeated fetches within
  one process. A failure is **not** cached: a later call retries the provider
  rather than serving a value that could not be refreshed.
* **Redacted failures.** The exception message names the provider and the
  reference, never the resolved value. Callers redact SQL separately
  (``app.common.sql_guard``), and this layer must not reintroduce a value into
  an error string that the exception handler would then ship.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Default lifetime of a resolved secret in the process-local cache. Short by
#: design: it exists to collapse the burst of lookups within a single query,
#: not to be a credential store with its own invalidation problem.
DEFAULT_CACHE_TTL_SECONDS = 300.0


class SecretResolutionError(RuntimeError):
    """A configured secret reference could not be resolved.

    Raised when the provider errors, times out, or returns a payload that does
    not carry an access/secret pair. It is deliberately distinct from
    ``ValueError`` so callers can tell "the reference is broken" from "the
    statement is malformed", and its message never contains a secret value.
    """


@dataclass(frozen=True)
class SecretValue:
    """A resolved credential pair. Held in memory only, never persisted."""

    access_key: str
    secret_key: str
    session_token: str = ""


@dataclass(frozen=True)
class SecretResolutionRecord:
    """The auditable *fact* of one resolution attempt — provider + reference.

    Deliberately carries no value. This is what an audit row may persist: that
    Nova asked provider X to resolve reference Y and whether it succeeded.
    """

    provider: str
    reference: str
    succeeded: bool
    error_type: str = ""


@runtime_checkable
class SecretProvider(Protocol):
    """Adapter contract: a secret reference in, a credential pair out.

    Implementations must be synchronous and raise ``SecretResolutionError``
    (or let a lower-level exception propagate to
    ``resolve_secret_reference``, which normalises it) on any failure. They
    must never log or embed the resolved value in an exception message.
    """

    name: str

    def fetch(self, reference: str) -> SecretValue:
        """Resolve ``reference`` to a credential pair."""
        ...


class AwsSecretsManagerProvider:
    """``SecretProvider`` backed by AWS Secrets Manager (boto3).

    Two payload shapes are accepted, which covers the common conventions:

    * a JSON object — ``{"access_key": ..., "secret_key": ...}`` (aliases
      ``access_key_id`` / ``secret_key_id`` are also read), or
    * a plain ``"access_key:secret_key"`` string.

    The boto3 client is created lazily and reused for the process lifetime, so
    constructing the adapter does no I/O and importing it does not require AWS
    credentials.
    """

    name = "aws_secrets_manager"

    def __init__(self, *, region_name: str | None = None) -> None:
        self._region_name = region_name
        self._client = None
        self._client_lock = threading.Lock()

    def _boto_client(self):
        """Build the Secrets Manager client once, on first use."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import boto3

                    self._client = boto3.client(
                        "secretsmanager", region_name=self._region_name or None
                    )
        return self._client

    def fetch(self, reference: str) -> SecretValue:
        try:
            response = self._boto_client().get_secret_value(SecretId=reference)
        except Exception as exc:  # botocore raises a wide hierarchy
            raise SecretResolutionError(
                f"secret provider '{self.name}' failed for reference '{reference}': "
                f"{type(exc).__name__}"
            ) from exc

        raw = response.get("SecretString")
        if raw is None:
            # Binary secrets cannot carry the access/secret pair this layer
            # needs; the bytes are deliberately not included in the message.
            raise SecretResolutionError(
                f"secret provider '{self.name}' returned a binary secret for "
                f"reference '{reference}'"
            )
        return _parse_secret_payload(raw, provider=self.name, reference=reference)


def _parse_secret_payload(raw: str, *, provider: str, reference: str) -> SecretValue:
    """Parse a provider payload into a credential pair.

    JSON object first, then ``access:secret``. Anything else fails closed with
    a redacted message — a payload that cannot be understood must not be
    guessed at, because guessing is how the wrong principal ends up
    authenticating.
    """
    text = raw.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise SecretResolutionError(
                f"secret provider '{provider}' returned unparseable JSON for "
                f"reference '{reference}'"
            ) from exc
        if not isinstance(parsed, dict):
            raise SecretResolutionError(
                f"secret provider '{provider}' returned a non-object payload for "
                f"reference '{reference}'"
            )
        access = parsed.get("access_key") or parsed.get("access_key_id") or ""
        secret = parsed.get("secret_key") or parsed.get("secret_key_id") or ""
        token = parsed.get("session_token") or ""
        if not access or not secret:
            raise SecretResolutionError(
                f"secret provider '{provider}' payload for reference '{reference}' "
                "is missing access_key/secret_key"
            )
        return SecretValue(access_key=access, secret_key=secret, session_token=token)

    if ":" in text:
        access, _, secret = text.partition(":")
        if access and secret:
            return SecretValue(access_key=access, secret_key=secret)

    raise SecretResolutionError(
        f"secret provider '{provider}' returned an unrecognised payload for "
        f"reference '{reference}'"
    )


# ── Reference scheme handling ───────────────────────────────────────────────


def _split_scheme(reference: str) -> tuple[str | None, str]:
    """Split ``aws://name`` into ``("aws", "name")``; bare refs have no scheme.

    Only a short, scheme-like prefix is treated as a scheme. An ARN
    (``arn:aws:secretsmanager:...``) is returned untouched, so it reaches the
    AWS adapter verbatim.
    """
    scheme, separator, rest = reference.partition("://")
    if separator and scheme and rest and len(scheme) <= 32:
        return scheme, rest
    return None, reference


_SCHEME_ALIASES: dict[str, str] = {
    "aws": "aws",
    "aws-sm": "aws",
    "secretsmanager": "aws",
    "aws_secrets_manager": "aws",
}


# ── Resolution facts for audit ──────────────────────────────────────────────

#: Per-context buffer of resolution facts. ``resolve_secret_reference`` is
#: synchronous (it runs deep inside SQL preparation), so it cannot await the
#: async audit writer. Instead it appends the fact here and the async caller
#: drains the buffer and persists it — the fact survives the sync/async
#: boundary without a background task whose completion nothing waits for.
_resolution_facts: contextvars.ContextVar[tuple[SecretResolutionRecord, ...]] = (
    contextvars.ContextVar("nova_secret_resolution_facts", default=())
)


def _record_fact(record: SecretResolutionRecord) -> None:
    _resolution_facts.set((*_resolution_facts.get(), record))


def drain_secret_resolution_facts() -> list[SecretResolutionRecord]:
    """Return the facts recorded in this context and clear the buffer.

    Idempotent for a caller: a second drain with no intervening resolution
    returns an empty list.
    """
    facts = list(_resolution_facts.get())
    _resolution_facts.set(())
    return facts


# ── Process-local TTL cache ─────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, SecretValue]] = {}


def _cache_get(key: tuple[str, str]) -> SecretValue | None:
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        with _cache_lock:
            _cache.pop(key, None)
        return None
    return value


def _cache_put(key: tuple[str, str], value: SecretValue) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + DEFAULT_CACHE_TTL_SECONDS, value)


def clear_secret_cache() -> None:
    """Drop every cached secret. Used by tests and on config reload."""
    with _cache_lock:
        _cache.clear()


def resolve_secret_reference(
    reference: str,
    *,
    providers: dict[str, SecretProvider] | None = None,
    use_cache: bool = True,
) -> SecretValue:
    """Resolve ``reference`` to a credential pair via the matching provider.

    Args:
        reference: The secret reference, optionally scheme-prefixed
            (``aws://...``); a bare ARN defaults to AWS Secrets Manager.
        providers: Provider registry override, keyed by adapter name. Production
            callers leave this unset and get the built-in registry; tests inject
            a mock provider here.
        use_cache: Set false to bypass the TTL cache (tests, forced refresh).

    Raises:
        SecretResolutionError: if the scheme has no adapter, the provider fails,
            or the payload cannot be parsed. Never falls back to another
            credential source.
    """
    scheme, target = _split_scheme(reference)
    adapter_name = _SCHEME_ALIASES.get(scheme) if scheme else "aws"
    if adapter_name is None:
        raise SecretResolutionError(
            f"no secret provider registered for scheme '{scheme}'"
        )

    registry = providers if providers is not None else _default_registry()
    provider = registry.get(adapter_name)
    if provider is None:
        raise SecretResolutionError(
            f"no secret provider registered for scheme '{scheme or 'aws'}'"
        )

    cache_key = (provider.name, target)
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            _record_fact(
                SecretResolutionRecord(provider.name, target, succeeded=True)
            )
            return cached

    # A failure is intentionally not cached: the next call retries the
    # provider instead of serving a value that could not be refreshed.
    try:
        value = provider.fetch(target)
    except SecretResolutionError as exc:
        _record_fact(
            SecretResolutionRecord(
                provider.name, target, succeeded=False, error_type=type(exc).__name__
            )
        )
        raise
    except Exception as exc:
        _record_fact(
            SecretResolutionRecord(
                provider.name, target, succeeded=False, error_type=type(exc).__name__
            )
        )
        raise SecretResolutionError(
            f"secret provider '{provider.name}' failed for reference '{target}': "
            f"{type(exc).__name__}"
        ) from exc
    if use_cache:
        _cache_put(cache_key, value)
    _record_fact(SecretResolutionRecord(provider.name, target, succeeded=True))
    return value


_default_providers: dict[str, SecretProvider] | None = None


def _default_registry() -> dict[str, SecretProvider]:
    """The built-in provider registry, constructed on first use.

    Lazy so importing this module never touches boto3 or AWS configuration.
    """
    global _default_providers
    if _default_providers is None:
        _default_providers = {"aws": AwsSecretsManagerProvider()}
    return _default_providers
