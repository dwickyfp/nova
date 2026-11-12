"""AI provider and model registry service over actor-bound PostgreSQL metadata.

Reads and writes go through ``AIRegistryRepository`` in a transaction bound to
an explicit actor. Mutations run through ``AIConfigurationService`` so the
configuration change, immutable function definitions, registration jobs and
outbox wakeups commit together. Encrypted provider keys stay in the companion
credential table: responses carry only presence and the masked hint, and the
plaintext is produced only for an outbound provider request.

Metadata-only reads without a caller (assistant, embeddings, gateway) use the
explicit ``nova-ai`` SERVICE actor. Credential resolution with that actor
returns a key only in the agent workload; other workloads fail closed.
"""

import json
import logging
from contextlib import suppress
from typing import Any
from uuid import uuid4

import httpx

from app.common.crypto import EncryptionError, decrypt, encrypt
from app.common.metadata_actors import metadata_scope, service_actor
from app.common.ssrf_guard import (
    BlockedEndpointError,
    guarded_async_client,
    resolve_and_validate_url,
)
from app.core.config import settings
from app.modules.access_control.security_context import SecurityContext
from app.modules.ai_ml.metadata_service import AIConfigurationService
from app.modules.ai_ml.repository import (
    AIRegistryRepository,
    ModelDefinition,
    ProviderDefinition,
)
from app.modules.ai_ml.schemas import AIModelCreate

logger = logging.getLogger(__name__)

#: The SERVICE actor for metadata reads that have no calling user.
REGISTRY_READER = service_actor("ai")

_PROVIDER_FIELDS = (
    "id", "name", "type", "endpoint", "default_params", "is_active", "created_at",
    "created_by", "has_api_key", "api_key_masked",
)
_MODEL_FIELDS = (
    "id", "provider_id", "name", "display_name", "type", "max_tokens", "logical_alias",
    "revision", "dimensions", "modality", "metric", "default_params", "is_active",
    "created_at", "created_by",
)
_EMBEDDING_IMMUTABLE = ("name", "revision", "dimensions", "modality", "metric")


def _params_text(value: Any) -> str | None:
    return json.dumps(value) if value else None


def _api_key_ciphertext(value: str | None) -> str | None:
    """Encrypt a newly supplied key; an empty value means "no key"."""
    if not value:
        return None
    if value.startswith("enc:"):
        # Only the existing owner produces ciphertext; a caller cannot inject it.
        raise ValueError("Enter the provider API key itself")
    return encrypt(value)


class AIService:
    """Manage AI providers and models in the PostgreSQL metadata store."""

    # ── Projections ────────────────────────────────────────────

    @classmethod
    def _provider(cls, row: dict[str, Any]) -> dict[str, Any]:
        return cls._deserialize_row({name: row.get(name) for name in _PROVIDER_FIELDS})

    @classmethod
    def _model(cls, row: dict[str, Any]) -> dict[str, Any]:
        return cls._deserialize_row({name: row.get(name) for name in _MODEL_FIELDS})

    # ── Providers ──────────────────────────────────────────────

    async def list_providers(self, *, actor: SecurityContext | None = None) -> list[dict]:
        """Every live provider, with presence and a masked hint instead of the key."""
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            rows = await AIRegistryRepository(connection).providers()
        return [self._provider(row) for row in rows]

    async def get_provider(
        self, provider_id: str, *, actor: SecurityContext | None = None,
    ) -> dict | None:
        """One live provider; never includes the stored key."""
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            row = await AIRegistryRepository(connection).provider(provider_id)
        if row is None or row.get("is_deleted"):
            return None
        return self._provider(row)

    async def get_provider_api_key(
        self, provider_id: str, *, actor: SecurityContext | None = None,
    ) -> str | None:
        """The decrypted key for an outbound provider request. Internal use only.

        The database decides who may read ciphertext (agent workload for active
        providers, an administrator in the web workload). Anyone else gets
        ``None`` and the caller refuses the request.
        """
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            credential = await AIRegistryRepository(connection).credential(provider_id)
        if credential is None or not credential.ciphertext:
            return None
        try:
            return decrypt(credential.ciphertext) or None
        except EncryptionError:
            logger.warning("Stored AI provider key could not be decrypted")
            return None

    async def create_provider(
        self, data: dict, username: str, *, actor: SecurityContext,
    ) -> dict | None:
        """Register a provider; ``created_by`` is the bound actor (``username``)."""
        definition = ProviderDefinition(
            name=data["name"],
            type=data["type"],
            endpoint=data["endpoint"],
            encrypted_api_key=_api_key_ciphertext(data.get("api_key")),
            default_params=_params_text(data.get("default_params")),
            is_active=True,
        )
        async with metadata_scope(actor) as connection:
            change = await AIConfigurationService(connection).create_provider(
                str(uuid4()), definition,
            )
        return self._provider(change.record) if change.record else None

    async def update_provider(
        self, provider_id: str, data: dict, *, actor: SecurityContext,
    ) -> dict | None:
        """Merge the supplied fields into the live provider; an omitted key is kept."""
        async with metadata_scope(actor) as connection:
            registry = AIRegistryRepository(connection)
            existing = await registry.provider(provider_id)
            if existing is None or existing["is_deleted"]:
                return None
            ciphertext = _api_key_ciphertext(data.get("api_key"))
            if ciphertext is None:
                credential = await registry.credential(provider_id)
                ciphertext = credential.ciphertext if credential else None
            params = data.get("default_params", self._parsed(existing["default_params"]))
            definition = ProviderDefinition(
                name=data.get("name", existing["name"]),
                type=data.get("type", existing["type"]),
                endpoint=data.get("endpoint", existing["endpoint"]),
                encrypted_api_key=ciphertext,
                default_params=(
                    existing["default_params"] if "default_params" not in data
                    else _params_text(params)
                ),
                is_active=bool(data.get("is_active", existing["is_active"] is not False)),
            )
            change = await AIConfigurationService(connection).replace_provider(
                provider_id, definition, expected_revision=existing["metadata_revision"],
            )
        return self._provider(change.record) if change.record else None

    async def set_provider_api_key(
        self, provider_id: str, api_key: str, *, actor: SecurityContext,
    ) -> bool:
        """Rotate only the provider key. Returns False for an unknown provider."""
        updated = await self.update_provider(provider_id, {"api_key": api_key}, actor=actor)
        return updated is not None

    async def delete_provider(self, provider_id: str, *, actor: SecurityContext) -> bool:
        """Retire a provider with its models and the function aliases that use it."""
        async with metadata_scope(actor) as connection:
            existing = await AIRegistryRepository(connection).provider(provider_id)
            if existing is None or existing["is_deleted"]:
                return False
            await AIConfigurationService(connection).retire_provider(
                provider_id, expected_revision=existing["metadata_revision"],
            )
        return True

    # ── Models ─────────────────────────────────────────────────

    async def list_models(
        self, provider_id: str | None = None, *, actor: SecurityContext | None = None,
    ) -> list[dict]:
        """Live models, optionally of one provider."""
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            rows = await AIRegistryRepository(connection).models(provider_id or None)
        return [self._model(row) for row in rows]

    async def get_model(
        self, model_id: str, *, actor: SecurityContext | None = None,
    ) -> dict | None:
        async with metadata_scope(actor or REGISTRY_READER, read_only=True) as connection:
            row = await AIRegistryRepository(connection).model(model_id)
        if row is None or row.get("is_deleted"):
            return None
        return self._model(row)

    @staticmethod
    def _model_definition(data: dict[str, Any]) -> ModelDefinition:
        embedding = data["type"] == "embedding"
        return ModelDefinition(
            provider_id=data["provider_id"],
            name=data["name"],
            type=data["type"],
            display_name=data.get("display_name"),
            max_tokens=data.get("max_tokens"),
            logical_alias=data.get("logical_alias"),
            revision=data.get("revision"),
            dimensions=data.get("dimensions"),
            modality=data.get("modality") or ("text" if embedding else None),
            metric=data.get("metric") or ("cosine" if embedding else None),
            default_params=_params_text(data.get("default_params")),
            is_active=bool(data.get("is_active", True) is not False),
        )

    async def create_model(
        self, data: dict, username: str, *, actor: SecurityContext,
    ) -> dict | None:
        """Register a model under a live provider; ``created_by`` is the bound actor."""
        data = {**AIModelCreate.model_validate(data).model_dump(), "is_active": True}
        definition = self._model_definition(data)
        async with metadata_scope(actor) as connection:
            registry = AIRegistryRepository(connection)
            provider = await registry.provider(data["provider_id"])
            if provider is None or provider["is_deleted"]:
                raise ValueError("Provider not found")
            if data["type"] == "embedding":
                await self._assert_unique_alias(registry, data["logical_alias"])
            change = await AIConfigurationService(connection).create_model(
                str(uuid4()), definition,
            )
        return self._model(change.record) if change.record else None

    async def delete_model(self, model_id: str, *, actor: SecurityContext) -> bool:
        """Retire a model. A model still used by a function alias is refused."""
        async with metadata_scope(actor) as connection:
            existing = await AIRegistryRepository(connection).model(model_id)
            if existing is None or existing["is_deleted"]:
                return False
            cursor = await connection.execute(
                "SELECT 1 FROM nova_meta.config_model_aliases "
                "WHERE model_id=%s AND NOT is_deleted LIMIT 1",
                (model_id,),
            )
            if await cursor.fetchone() is not None:
                raise ValueError(
                    "This model is used by an AI function alias; change or delete the alias first"
                )
            await AIConfigurationService(connection).delete_model(
                model_id,
                provider_id=existing["provider_id"],
                expected_revision=existing["metadata_revision"],
            )
        return True

    async def update_model(
        self, model_id: str, data: dict, *, actor: SecurityContext,
    ) -> dict | None:
        """Merge the supplied fields into the live model."""
        async with metadata_scope(actor) as connection:
            registry = AIRegistryRepository(connection)
            row = await registry.model(model_id)
            if row is None or row["is_deleted"]:
                return None
            existing = self._model(row)
            if data.get("type", existing["type"]) != existing["type"]:
                raise ValueError("Model type cannot be changed after creation")
            if existing["type"] == "embedding":
                for field in _EMBEDDING_IMMUTABLE:
                    if field in data and data[field] != existing.get(field):
                        raise ValueError(f"Embedding {field} is immutable; register a new model")
            merged = {**existing, **data}
            validated = AIModelCreate.model_validate(merged)
            if validated.type == "embedding":
                await self._assert_unique_alias(
                    registry, validated.logical_alias, exclude_id=model_id,
                )
            change = await AIConfigurationService(connection).replace_model(
                model_id,
                self._model_definition(merged),
                expected_revision=row["metadata_revision"],
            )
        return self._model(change.record) if change.record else None

    @staticmethod
    async def _assert_unique_alias(
        registry: AIRegistryRepository, alias: str | None, *, exclude_id: str | None = None,
    ) -> None:
        if not alias:
            return
        for model in await registry.models():
            if (
                model["type"] == "embedding" and model["logical_alias"] == alias
                and model["id"] != exclude_id
            ):
                raise ValueError("Embedding alias already exists")

    # ── Test Connection ─────────────────────────────────────────

    async def test_connection(
        self,
        provider_type: str,
        endpoint: str,
        api_key: str | None,
        *,
        allow_private_network: bool = False,
    ) -> dict:
        """Test connectivity to an LLM provider by hitting its /models endpoint.

        For openai/openai_compatible: GET {endpoint}/models
        For anthropic: GET {endpoint}/models (Anthropic v1 API)

        Returns dict with: success, message, models (list of model IDs).

        The user-supplied ``endpoint`` is untrusted: it is run through the SSRF
        guard before any network activity, and the request itself uses the
        guard's transport so redirects are checked at every hop (NOVA-107).
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

        # Reject blocked targets before the request is built. Loopback and
        # private addresses pass only when the caller may store a provider.
        # The message is fixed and carries no resolved address.
        try:
            resolve_and_validate_url(
                url, allow_private_network=allow_private_network
            )
        except BlockedEndpointError:
            logger.warning(
                "Blocked test-connection to a non-public endpoint (provider=%s)",
                provider_type,
            )
            return {
                "success": False,
                "message": (
                    "Endpoint is not allowed: use an http(s) URL on the internet "
                    "or your local network"
                ),
                "models": [],
            }

        try:
            async with guarded_async_client(
                timeout=15.0,
                allow_private_network=allow_private_network,
                loopback_host=settings.AI_PROVIDER_LOOPBACK_HOST,
            ) as client:
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
        except BlockedEndpointError:
            # A redirect (or a rebound DNS answer) landed on a non-public
            # address. Same public message as the pre-check; no address leaked.
            logger.warning(
                "Blocked test-connection redirect to a non-public endpoint "
                "(provider=%s)",
                provider_type,
            )
            return {
                "success": False,
                "message": (
                    "Endpoint is not allowed: use an http(s) URL on the internet "
                    "or your local network"
                ),
                "models": [],
            }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": f"Connection timed out after 15s — {url}",
                "models": [],
            }
        except Exception:
            logger.exception("Unexpected error testing connection to %s", url)
            return {
                "success": False,
                "message": "Unexpected error testing connection",
                "models": [],
            }

    # ── Internal helpers ───────────────────────────────────────

    @staticmethod
    def _parsed(value: Any) -> Any:
        if isinstance(value, str):
            with suppress(json.JSONDecodeError, TypeError):
                return json.loads(value)
        return value

    @classmethod
    def _deserialize_row(cls, row: dict) -> dict:
        """Parse the stored JSON text of ``default_params``."""
        result = dict(row)
        if result.get("default_params"):
            result["default_params"] = cls._parsed(result["default_params"])
        return result


# Singleton
ai_service = AIService()
