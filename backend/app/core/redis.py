"""Redis-backed session store. Stores encrypted DB passwords per session."""

import uuid

import redis.asyncio as aioredis

from app.core.config import settings

SESSION_PREFIX = "nova:session:"


class SessionStore:
    """Redis session store for authenticated user sessions.

    Each session stores:
    - username: StarRocks user
    - encrypted_password: Fernet-encrypted DB password
    - roles: comma-separated StarRocks roles
    """

    def __init__(self):
        self._redis: aioredis.Redis | None = None

    async def init(self) -> None:
        """Initialize Redis connection. Call once at startup."""
        self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    async def close(self) -> None:
        """Close Redis connection. Call at shutdown."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def create(
        self,
        username: str,
        encrypted_password: str,
        roles: list[str],
        *,
        default_role: str | None = None,
        active_role: str | None = None,
    ) -> str:
        """Create a new session. Returns session_id (UUID)."""
        if not self._redis:
            raise RuntimeError("SessionStore not initialized. Call init() first.")

        session_id = str(uuid.uuid4())
        if settings.RANGER_ENABLED:
            if not default_role or default_role not in roles:
                raise ValueError("A valid explicit default role is required")
            if not active_role or active_role not in roles:
                raise ValueError("A valid active role is required")
        data = {
            "username": username,
            "encrypted_password": encrypted_password,
            "roles": ",".join(roles),
            "assigned_roles": ",".join(roles),
            "default_role": default_role or "",
            "active_role": active_role or "",
            "security_context_version": "1",
        }
        key = f"{SESSION_PREFIX}{session_id}"
        await self._redis.hset(key, mapping=data)
        await self._redis.expire(key, settings.SESSION_TTL_SECONDS)
        return session_id

    async def get(self, session_id: str) -> dict | None:
        """Retrieve session data. Returns None if expired or missing."""
        if not self._redis:
            return None

        data = await self._redis.hgetall(f"{SESSION_PREFIX}{session_id}")
        if not data:
            return None
        roles_value = data.get("assigned_roles") or data.get("roles", "")
        data["roles"] = roles_value.split(",") if roles_value else []
        data["assigned_roles"] = list(data["roles"])
        data["default_role"] = data.get("default_role") or None
        data["active_role"] = data.get("active_role") or None
        data["security_context_version"] = int(
            data.get("security_context_version") or 1
        )
        return data

    async def set_active_role(self, session_id: str, active_role: str) -> int:
        """Commit a verified active role and advance the security boundary."""
        if not self._redis:
            raise RuntimeError("SessionStore not initialized. Call init() first.")

        key = f"{SESSION_PREFIX}{session_id}"
        current = await self.get(session_id)
        if not current or active_role not in current["assigned_roles"]:
            raise ValueError("Role is not assigned to this session")
        version = int(current.get("security_context_version") or 1) + 1
        await self._redis.hset(
            key,
            mapping={
                "active_role": active_role,
                "security_context_version": str(version),
            },
        )
        await self._redis.expire(key, settings.SESSION_TTL_SECONDS)
        return version

    async def delete(self, session_id: str) -> None:
        """Delete a session (logout)."""
        if self._redis:
            await self._redis.delete(f"{SESSION_PREFIX}{session_id}")

    async def refresh(self, session_id: str) -> None:
        """Refresh session TTL on activity."""
        if self._redis:
            await self._redis.expire(f"{SESSION_PREFIX}{session_id}", settings.SESSION_TTL_SECONDS)


# Singleton — initialized in main.py lifespan
session_store = SessionStore()
