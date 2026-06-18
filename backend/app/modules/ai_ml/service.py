"""AI Provider Management service — CRUD for providers and models via direct asyncmy connections.

Tables:
  NOVA_SYSTEM.CONFIG_AI_PROVIDERS  — registered AI providers (OpenAI, Anthropic, etc.)
  NOVA_SYSTEM.CONFIG_AI_MODELS     — models registered under each provider
"""

import json
import logging
from uuid import uuid4

import asyncmy
import asyncmy.cursors

from app.core.config import settings

logger = logging.getLogger(__name__)


class AIService:
    """Manage AI providers and models in NOVA_SYSTEM.

    Every method opens a fresh root-level asyncmy connection, executes,
    and closes. This keeps the service stateless and avoids pool contention
    with the system pool used elsewhere.
    """

    # ── DB helpers ──────────────────────────────────────────────

    @staticmethod
    async def _connect() -> asyncmy.Connection:
        """Open a root-level connection to StarRocks."""
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            autocommit=True,
            connect_timeout=10,
        )

    # ── Providers ──────────────────────────────────────────────

    async def list_providers(self) -> list[dict]:
        """List all registered AI providers."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, type, endpoint, api_key_env, "
                    "default_params, is_active, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS ORDER BY name"
                )
                rows = await cur.fetchall()
                return [self._deserialize_row(row) for row in rows]
        finally:
            conn.close()

    async def get_provider(self, provider_id: str) -> dict | None:
        """Get a single provider by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, type, endpoint, api_key_env, "
                    "default_params, is_active, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS WHERE id = %s",
                    (provider_id,),
                )
                row = await cur.fetchone()
                return self._deserialize_row(row) if row else None
        finally:
            conn.close()

    async def create_provider(self, data: dict, username: str) -> dict | None:
        """INSERT a new AI provider. Returns the created provider."""
        provider_id = str(uuid4())
        default_params_json = (
            json.dumps(data.get("default_params")) if data.get("default_params") else None
        )
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_AI_PROVIDERS "
                    "(id, name, type, endpoint, api_key_env, default_params, "
                    "is_active, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, true, NOW(), %s)",
                    (
                        provider_id,
                        data["name"],
                        data["type"],
                        data["endpoint"],
                        data.get("api_key_env"),
                        default_params_json,
                        username,
                    ),
                )
            return await self.get_provider(provider_id)
        finally:
            conn.close()

    async def delete_provider(self, provider_id: str) -> bool:
        """DELETE a provider by ID. Cascades to delete all models under this provider."""
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                # Cascade: delete models first
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AI_MODELS WHERE provider_id = %s",
                    (provider_id,),
                )
                # Then delete the provider
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS WHERE id = %s",
                    (provider_id,),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Models ─────────────────────────────────────────────────

    async def list_models(self, provider_id: str | None = None) -> list[dict]:
        """List all AI models, optionally filtered by provider_id."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                if provider_id:
                    await cur.execute(
                        "SELECT id, provider_id, name, display_name, type, "
                        "max_tokens, default_params, is_active, created_at, created_by "
                        "FROM NOVA_SYSTEM.CONFIG_AI_MODELS "
                        "WHERE provider_id = %s ORDER BY name",
                        (provider_id,),
                    )
                else:
                    await cur.execute(
                        "SELECT id, provider_id, name, display_name, type, "
                        "max_tokens, default_params, is_active, created_at, created_by "
                        "FROM NOVA_SYSTEM.CONFIG_AI_MODELS ORDER BY name"
                    )
                rows = await cur.fetchall()
                return [self._deserialize_row(row) for row in rows]
        finally:
            conn.close()

    async def get_model(self, model_id: str) -> dict | None:
        """Get a single model by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, provider_id, name, display_name, type, "
                    "max_tokens, default_params, is_active, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_AI_MODELS WHERE id = %s",
                    (model_id,),
                )
                row = await cur.fetchone()
                return self._deserialize_row(row) if row else None
        finally:
            conn.close()

    async def create_model(self, data: dict, username: str) -> dict | None:
        """INSERT a new AI model. Returns the created model."""
        model_id = str(uuid4())
        default_params_json = (
            json.dumps(data.get("default_params")) if data.get("default_params") else None
        )
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_AI_MODELS "
                    "(id, provider_id, name, display_name, type, max_tokens, "
                    "default_params, is_active, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, true, NOW(), %s)",
                    (
                        model_id,
                        data["provider_id"],
                        data["name"],
                        data.get("display_name"),
                        data["type"],
                        data.get("max_tokens"),
                        default_params_json,
                        username,
                    ),
                )
            return await self.get_model(model_id)
        finally:
            conn.close()

    async def delete_model(self, model_id: str) -> bool:
        """DELETE a model by ID. Returns True if a row was deleted."""
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM NOVA_SYSTEM.CONFIG_AI_MODELS WHERE id = %s",
                    (model_id,),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Internal helpers ───────────────────────────────────────

    @staticmethod
    def _deserialize_row(row: dict) -> dict:
        """Deserialize a DB row, parsing JSON fields (default_params)."""
        result = dict(row)
        if result.get("default_params") and isinstance(result["default_params"], str):
            try:
                result["default_params"] = json.loads(result["default_params"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result


# Singleton
ai_service = AIService()
