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
import httpx

from app.core.config import settings
from app.common.crypto import encrypt, decrypt

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
                    "SELECT id, name, type, endpoint, api_key, "
                    "default_params, is_active, created_at, created_by "
                    "FROM NOVA_SYSTEM.CONFIG_AI_PROVIDERS ORDER BY name"
                )
                rows = await cur.fetchall()
                result = []
                for row in rows:
                    d = self._deserialize_row(row)
                    d["api_key"] = decrypt(d.get("api_key"))
                    result.append(d)
                return result
        finally:
            conn.close()

    async def get_provider(self, provider_id: str) -> dict | None:
        """Get a single provider by ID."""
        conn = await self._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, name, type, endpoint, api_key, "
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
                    "(id, name, type, endpoint, api_key, default_params, "
                    "is_active, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, true, NOW(), %s)",
                    (
                        provider_id,
                        data["name"],
                        data["type"],
                        data["endpoint"],
                        encrypt(data.get("api_key")),
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

    async def update_provider(self, provider_id: str, data: dict) -> dict | None:
        """Update an AI provider. Uses INSERT to replace (Primary Key table auto-upsert).

        Only fields present in `data` are updated; existing values are preserved.
        """
        existing = await self.get_provider(provider_id)
        if not existing:
            return None

        merged = {
            "name": data.get("name", existing["name"]),
            "type": data.get("type", existing["type"]),
            "endpoint": data.get("endpoint", existing["endpoint"]),
            "api_key": encrypt(data.get("api_key", decrypt(existing.get("api_key")))),
            "default_params": data.get("default_params", existing.get("default_params")),
            "is_active": data.get("is_active", existing.get("is_active", True)),
            "created_at": existing.get("created_at"),
            "created_by": existing.get("created_by"),
        }

        default_params_json = (
            json.dumps(merged["default_params"])
            if merged["default_params"]
            else None
        )

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_AI_PROVIDERS "
                    "(id, name, type, endpoint, api_key, default_params, "
                    "is_active, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        provider_id,
                        merged["name"],
                        merged["type"],
                        merged["endpoint"],
                        encrypt(merged["api_key"]) if merged.get("api_key") else None,
                        default_params_json,
                        merged["is_active"],
                        merged["created_at"],
                        merged["created_by"],
                    ),
                )
            return await self.get_provider(provider_id)
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
                result = []
                for row in rows:
                    d = self._deserialize_row(row)
                    d["api_key"] = decrypt(d.get("api_key"))
                    result.append(d)
                return result
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

    async def update_model(self, model_id: str, data: dict) -> dict | None:
        """Update an AI model. Uses INSERT to replace (Primary Key table auto-upsert).

        Only fields present in `data` are updated; existing values are preserved.
        """
        existing = await self.get_model(model_id)
        if not existing:
            return None

        merged = {
            "provider_id": existing["provider_id"],
            "name": data.get("name", existing["name"]),
            "display_name": data.get("display_name", existing.get("display_name")),
            "type": data.get("type", existing["type"]),
            "max_tokens": data.get("max_tokens", existing.get("max_tokens")),
            "default_params": data.get("default_params", existing.get("default_params")),
            "is_active": data.get("is_active", existing.get("is_active", True)),
            "created_at": existing.get("created_at"),
            "created_by": existing.get("created_by"),
        }

        default_params_json = (
            json.dumps(merged["default_params"])
            if merged["default_params"]
            else None
        )

        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_AI_MODELS "
                    "(id, provider_id, name, display_name, type, max_tokens, "
                    "default_params, is_active, created_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        model_id,
                        merged["provider_id"],
                        merged["name"],
                        merged["display_name"],
                        merged["type"],
                        merged["max_tokens"],
                        default_params_json,
                        merged["is_active"],
                        merged["created_at"],
                        merged["created_by"],
                    ),
                )
            return await self.get_model(model_id)
        finally:
            conn.close()

    # ── Test Connection ─────────────────────────────────────────

    async def test_connection(
        self, provider_type: str, endpoint: str, api_key: str | None
    ) -> dict:
        """Test connectivity to an LLM provider by hitting its /models endpoint.

        For openai/openai_compatible: GET {endpoint}/models
        For anthropic: GET {endpoint}/models (Anthropic v1 API)

        Returns dict with: success, message, models (list of model IDs).
        """
        # Build request based on provider type
        base_url = endpoint.rstrip("/")
        headers: dict[str, str] = {}

        if provider_type in ("openai", "openai_compatible"):
            url = f"{base_url}/models"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        elif provider_type == "anthropic":
            url = f"{base_url}/models"
            if api_key:
                headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            return {
                "success": False,
                "message": f"Unsupported provider type: {provider_type}",
                "models": [],
            }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                # OpenAI-compatible: {"data": [{"id": "gpt-4o"}, ...]}
                # Anthropic: {"data": [{"id": "claude-3-..."}, ...]}
                models: list[str] = []
                if isinstance(data, dict) and "data" in data:
                    for item in data["data"]:
                        model_id = item.get("id") if isinstance(item, dict) else str(item)
                        if model_id:
                            models.append(str(model_id))
                elif isinstance(data, list):
                    for item in data:
                        model_id = item.get("id") if isinstance(item, dict) else str(item)
                        if model_id:
                            models.append(str(model_id))

                return {
                    "success": True,
                    "message": f"Connection successful — {len(models)} model(s) available",
                    "models": sorted(models),
                }

            # Non-200: try to extract error message
            try:
                err_data = resp.json()
                err_msg = (
                    err_data.get("error", {}).get("message")
                    if isinstance(err_data.get("error"), dict)
                    else err_data.get("error")
                    or err_data.get("message")
                    or resp.text[:200]
                )
            except Exception:
                err_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"

            return {
                "success": False,
                "message": f"HTTP {resp.status_code}: {err_msg}",
                "models": [],
            }

        except httpx.ConnectError:
            return {
                "success": False,
                "message": f"Cannot connect to {url} — check endpoint URL and network",
                "models": [],
            }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": f"Connection timed out after 15s — {url}",
                "models": [],
            }
        except Exception as e:
            logger.exception("Unexpected error testing connection to %s", url)
            return {
                "success": False,
                "message": f"Unexpected error: {e}",
                "models": [],
            }

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
