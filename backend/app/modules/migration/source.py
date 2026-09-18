"""Resolve a registered migration source into a real StarRocks connection.

Finding 1 (High) from QA: a source was registerable but never consumed, so
enumerate/dry-run silently read Nova's *local* engine. This module is the fix:
a registered source carries its own cluster address (host, port, username) and a
**secret reference** for its password, and ``open_source_connection`` opens a
connection to that cluster.

Credential invariant (AGENTS.md §2): the registry persists the *reference* to the
password, never its value. ``resolve_source_password`` fetches the value from
``app.storage.secrets`` at call time and returns it in memory only. A broken
reference raises ``SourceConnectionError`` and never falls back to another
principal (the same fail-closed rule as ``resolve_storage_credentials``).

No method here executes a migration; ``open_source_connection`` is a read-only
metadata connection used by enumerate and dry-run.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncmy
import asyncmy.cursors

from app.storage.secrets import SecretResolutionError, resolve_secret_reference


class SourceConnectionError(RuntimeError):
    """A registered source could not be addressed or reached.

    Distinct from a plain ``ValueError`` so the router can map "unknown source"
    and "source unreachable" to different status codes. The message never
    contains a credential value.
    """


@dataclass(frozen=True)
class SourceConnection:
    """A resolved source cluster address. Carries no credential value."""

    name: str
    host: str
    port: int
    username: str
    #: Reference to the password in the secret store; empty means "no password
    #: configured" (the same convention the local test engine uses).
    secret_ref: str = ""

    def password(self) -> str:
        """Resolve the password in memory. Never persisted, never logged.

        The existing secret provider returns an ``(access_key, secret_key)``
        pair; for a StarRocks source the ``secret_key`` is the password. An
        ``access_key`` override is deliberately not honoured here because the
        source username is an explicit field — mixing the two would let a
        storage-shaped secret silently authenticate as a different user.
        """
        if not self.secret_ref:
            return ""
        try:
            value = resolve_secret_reference(self.secret_ref)
        except SecretResolutionError as exc:
            # Fail closed: an opted-in reference that cannot resolve must never
            # fall back to an inline or default principal.
            raise SourceConnectionError(
                f"Could not resolve the secret reference for source '{self.name}'"
            ) from exc
        return value.secret_key


def connection_from_row(row: dict) -> SourceConnection:
    """Build a ``SourceConnection`` from a registry row.

    Raises ``SourceConnectionError`` when the row cannot address a cluster — a
    legacy row written before this revision has no host and must not be treated
    as the local engine.
    """
    host = (row.get("host") or "").strip()
    if not host:
        raise SourceConnectionError(
            f"Source '{row.get('name', '?')}' has no host; it cannot be used to "
            "reach a source cluster"
        )
    return SourceConnection(
        name=row["name"],
        host=host,
        port=int(row.get("port") or 9030),
        username=(row.get("username") or "root").strip(),
        secret_ref=(row.get("secret_ref") or "").strip(),
    )


@asynccontextmanager
async def open_source_connection(
    source: SourceConnection,
) -> AsyncGenerator[asyncmy.Connection, None]:
    """Open a read-only MySQL-protocol connection to the source cluster."""
    try:
        conn = await asyncmy.connect(
            host=source.host,
            port=source.port,
            user=source.username,
            password=source.password(),
            autocommit=True,
            connect_timeout=10,
        )
    except Exception as exc:
        raise SourceConnectionError(f"Could not connect to source '{source.name}'") from exc
    try:
        yield conn
    finally:
        conn.close()
