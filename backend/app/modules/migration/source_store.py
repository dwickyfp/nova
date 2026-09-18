"""Server-side store for an in-flight migration source connection.

The source password must survive the connect → enumerate → dry-run sequence
without ever reaching the client, the model layer, or a log. It lives here, in
Redis, encrypted at rest with the same Fernet key the session store uses, keyed
by the authenticated session. The client holds only a ``connection_id`` — the
key, not the secret.

This is deliberately not ``NOVA_SYSTEM``: it is ephemeral working state with a
short TTL, and it must expire on its own.
"""

from __future__ import annotations

import json

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.security import decrypt_password, encrypt_password

#: Namespace for a source connection, distinct from ``nova:session:``.
SOURCE_PREFIX = "nova:migration:source:"

#: Source connections are working state for a wizard run, not durable config.
SOURCE_TTL_SECONDS = 3600


class MigrationSourceNotFound(ValueError):
    """No live source connection exists for this id (expired or never made)."""


class MigrationSourceStore:
    """Redis-backed storage for an encrypted source connection."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def init(self) -> None:
        self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None

    def _require(self) -> aioredis.Redis:
        if not self._redis:
            raise RuntimeError("MigrationSourceStore not initialized.")
        return self._redis

    async def put(
        self,
        connection_id: str,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str | None = None,
    ) -> None:
        """Persist a source connection, encrypting the password before storage."""
        payload = json.dumps(
            {
                "host": host,
                "port": port,
                "username": username,
                "encrypted_password": encrypt_password(password),
                "database": database,
            }
        )
        redis = self._require()
        await redis.set(f"{SOURCE_PREFIX}{connection_id}", payload)
        await redis.expire(f"{SOURCE_PREFIX}{connection_id}", SOURCE_TTL_SECONDS)

    async def get(self, connection_id: str) -> dict:
        """Return the connection with its password still Fernet-encrypted."""
        redis = self._require()
        raw = await redis.get(f"{SOURCE_PREFIX}{connection_id}")
        if not raw:
            raise MigrationSourceNotFound(connection_id)
        # Refresh on use so a slow wizard run is not cut off mid-flow.
        await redis.expire(f"{SOURCE_PREFIX}{connection_id}", SOURCE_TTL_SECONDS)
        return json.loads(raw)

    async def delete(self, connection_id: str) -> None:
        redis = self._require()
        await redis.delete(f"{SOURCE_PREFIX}{connection_id}")

    async def password(self, connection_id: str) -> str:
        """Decrypt the stored password for an outbound connection.

        Only the repository calls this, and only to open a socket.
        """
        data = await self.get(connection_id)
        return decrypt_password(data["encrypted_password"])


migration_source_store = MigrationSourceStore()
