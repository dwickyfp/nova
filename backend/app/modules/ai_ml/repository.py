"""Actor-bound AI registry storage for the PostgreSQL cutover.

Services own the transaction and public JSON projection. Credential records are
internal inputs to provider invocation, never API response models.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg.pq import TransactionStatus

from app.common.crypto import EncryptionError, decrypt, mask_secret
from app.core.metadata_db import MetadataConflict, MetadataIntegrityViolation
from app.modules.ai_ml.schemas import AIModelCreate


def _text(value: str, maximum: int, *, empty: bool = False) -> None:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (
        not empty and not value
    ):
        raise ValueError("Invalid AI registry value")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid AI registry value") from None


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid registry JSON")


def _json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate registry JSON fields")
    return value


def _parameter_values(value: Any) -> None:
    if isinstance(value, str):
        _text(value, 65533, empty=True)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Invalid registry JSON")
    elif isinstance(value, list):
        for item in value:
            _parameter_values(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _parameter_values(key)
            _parameter_values(item)


def _parameters(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    _text(value, 65533)
    try:
        parsed = json.loads(value, object_pairs_hook=_json_fields, parse_constant=_reject_constant)
        if not isinstance(parsed, dict):
            raise ValueError()
        _parameter_values(parsed)
        return parsed
    except (ValueError, RecursionError):
        raise ValueError("Invalid registry JSON") from None


def _key_projection(ciphertext: str | None) -> tuple[bool, str | None]:
    if ciphertext is None:
        return False, None
    _text(ciphertext, 4096)
    if not ciphertext.startswith("enc:"):
        raise ValueError("Provider credential must be authenticated ciphertext")
    try:
        plaintext = decrypt(ciphertext)
        if not plaintext:
            raise ValueError()
        _text(plaintext, 512)
        return True, mask_secret(plaintext)
    except (EncryptionError, ValueError):
        raise ValueError("Provider credential must be authenticated ciphertext") from None


def _revision(value: int) -> None:
    if type(value) is not int or not 1 <= value < 2**63 - 1:
        raise ValueError("Invalid AI registry revision")


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    name: str
    type: str
    endpoint: str = field(repr=False)
    encrypted_api_key: str | None = field(default=None, repr=False)
    default_params: str | None = field(default=None, repr=False)
    is_active: bool = True

    def __post_init__(self) -> None:
        _text(self.name, 128)
        _text(self.type, 32)
        _text(self.endpoint, 512)
        try:
            url = urlsplit(self.endpoint)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None:
                raise ValueError()
        except ValueError:
            raise ValueError("Invalid provider endpoint") from None
        if type(self.is_active) is not bool:
            raise ValueError("Invalid provider state")
        _key_projection(self.encrypted_api_key)
        _parameters(self.default_params)


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    provider_id: str
    name: str
    type: str
    display_name: str | None = None
    max_tokens: int | None = None
    logical_alias: str | None = None
    revision: str | None = None
    dimensions: int | None = None
    modality: str | None = None
    metric: str | None = None
    default_params: str | None = field(default=None, repr=False)
    is_active: bool = True

    def __post_init__(self) -> None:
        _text(self.provider_id, 64)
        for value, maximum, allow_empty in (
            (self.name, 128, False), (self.display_name, 256, True),
            (self.logical_alias, 128, False), (self.revision, 128, False),
        ):
            if value is not None:
                _text(value, maximum, empty=allow_empty)
        if type(self.is_active) is not bool:
            raise ValueError("Invalid model state")
        for number in (self.max_tokens, self.dimensions):
            if number is not None and (type(number) is not int or not 0 < number < 2**31):
                raise ValueError("Invalid model integer")
        try:
            AIModelCreate.model_validate(
                {**asdict(self), "default_params": _parameters(self.default_params)}, strict=True,
            )
        except ValueError:
            raise ValueError("Invalid AI model definition") from None
        if self.type == "embedding" and (self.modality is None or self.metric is None):
            raise ValueError("Embedding modality and metric must be explicit")


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    provider: dict[str, Any] = field(repr=False)
    ciphertext: str | None = field(repr=False)


_PROVIDER_COLUMNS = (
    "id, name, type, endpoint, default_params, is_active, created_at, created_by, "
    "metadata_revision, is_deleted, has_api_key, api_key_masked"
)
_MODEL_COLUMNS = (
    "id, provider_id, name, display_name, type, max_tokens, logical_alias, revision, "
    "dimensions, modality, metric, default_params, is_active, created_at, created_by, "
    "metadata_revision, is_deleted"
)
_MODEL_FIELDS = (
    "name", "display_name", "max_tokens", "logical_alias", "revision", "dimensions",
    "modality", "metric", "default_params", "is_active",
)


class AIRegistryRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("AI registry requires an active metadata transaction")

    async def providers(self) -> list[dict[str, Any]]:
        self._require_transaction()
        cursor = await self._connection.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM nova_meta.config_ai_providers "
            "WHERE NOT is_deleted ORDER BY name, id"
        )
        return await cursor.fetchall()

    async def provider(self, identity: str) -> dict[str, Any] | None:
        self._require_transaction()
        _text(identity, 64)
        cursor = await self._connection.execute(
            f"SELECT {_PROVIDER_COLUMNS} FROM nova_meta.config_ai_providers WHERE id=%s",
            (identity,),
        )
        return await cursor.fetchone()

    async def credential(self, identity: str) -> ProviderCredential | None:
        self._require_transaction()
        _text(identity, 64)
        cursor = await self._connection.execute(
            "SELECT " + ", ".join("p." + name.strip() for name in _PROVIDER_COLUMNS.split(","))
            + ", c.ciphertext FROM nova_meta.config_ai_providers p "
            "JOIN nova_meta.config_ai_provider_credentials c "
            "ON c.provider_id=p.id AND c.metadata_revision=p.metadata_revision "
            "WHERE p.id=%s AND NOT p.is_deleted AND (p.is_active IS TRUE "
            "OR (current_user='nova_meta_web' AND nova_meta.actor_is_administrator()))",
            (identity,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        ciphertext = row.pop("ciphertext")
        return ProviderCredential(row, ciphertext)

    async def create_provider(
        self, identity: str, definition: ProviderDefinition,
    ) -> dict[str, Any]:
        self._require_transaction()
        _text(identity, 64)
        has_key, hint = _key_projection(definition.encrypted_api_key)
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                "INSERT INTO nova_meta.config_ai_providers "
                "(id,name,type,endpoint,default_params,is_active,has_api_key,api_key_masked) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING "
                f"RETURNING {_PROVIDER_COLUMNS}",
                (identity, definition.name, definition.type, definition.endpoint,
                 definition.default_params, definition.is_active, has_key, hint),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            await self._connection.execute(
                "INSERT INTO nova_meta.config_ai_provider_credentials "
                "(provider_id,metadata_revision,ciphertext) VALUES (%s,1,%s)",
                (identity, definition.encrypted_api_key),
            )
            return row

    async def replace_provider(
        self, identity: str, definition: ProviderDefinition, *, expected_revision: int,
    ) -> dict[str, Any]:
        self._require_transaction()
        _text(identity, 64)
        _revision(expected_revision)
        has_key, hint = _key_projection(definition.encrypted_api_key)
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                "UPDATE nova_meta.config_ai_providers SET name=%s,type=%s,endpoint=%s, "
                "default_params=%s,is_active=%s,has_api_key=%s,api_key_masked=%s, "
                "metadata_revision=metadata_revision+1 "
                "WHERE id=%s AND metadata_revision=%s AND NOT is_deleted "
                f"RETURNING {_PROVIDER_COLUMNS}",
                (definition.name, definition.type, definition.endpoint, definition.default_params,
                 definition.is_active, has_key, hint, identity, expected_revision),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            await self._replace_credential(
                identity, expected_revision, definition.encrypted_api_key,
            )
            return row

    async def _replace_credential(self, identity: str, revision: int, value: str | None) -> None:
        cursor = await self._connection.execute(
            "UPDATE nova_meta.config_ai_provider_credentials "
            "SET ciphertext=%s,metadata_revision=metadata_revision+1 "
            "WHERE provider_id=%s AND metadata_revision=%s RETURNING provider_id",
            (value, identity, revision),
        )
        if await cursor.fetchone() is None:
            raise MetadataIntegrityViolation()

    async def delete_provider(self, identity: str, *, expected_revision: int) -> None:
        self._require_transaction()
        _text(identity, 64)
        _revision(expected_revision)
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                "SELECT id FROM nova_meta.config_ai_providers "
                "WHERE id=%s AND metadata_revision=%s AND NOT is_deleted FOR UPDATE",
                (identity, expected_revision),
            )
            if await cursor.fetchone() is None:
                raise MetadataConflict()
            await self._connection.execute(
                "UPDATE nova_meta.config_ai_models SET is_deleted=true,is_active=false, "
                "metadata_revision=metadata_revision+1 WHERE provider_id=%s AND NOT is_deleted",
                (identity,),
            )
            await self._connection.execute(
                "UPDATE nova_meta.config_ai_providers SET is_deleted=true,is_active=false, "
                "has_api_key=false,api_key_masked=NULL,metadata_revision=metadata_revision+1 "
                "WHERE id=%s", (identity,),
            )
            await self._replace_credential(identity, expected_revision, None)

    async def models(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        self._require_transaction()
        if provider_id is not None:
            _text(provider_id, 64)
        cursor = await self._connection.execute(
            f"SELECT {_MODEL_COLUMNS} FROM nova_meta.config_ai_models "
            "WHERE NOT is_deleted AND (%s::text IS NULL OR provider_id=%s) ORDER BY name, id",
            (provider_id, provider_id),
        )
        return await cursor.fetchall()

    async def model(self, identity: str) -> dict[str, Any] | None:
        self._require_transaction()
        _text(identity, 64)
        cursor = await self._connection.execute(
            f"SELECT {_MODEL_COLUMNS} FROM nova_meta.config_ai_models WHERE id=%s", (identity,),
        )
        return await cursor.fetchone()

    async def _lock_provider(self, identity: str) -> None:
        cursor = await self._connection.execute(
            "SELECT id FROM nova_meta.config_ai_providers "
            "WHERE id=%s AND NOT is_deleted FOR UPDATE",
            (identity,),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()

    async def create_model(self, identity: str, definition: ModelDefinition) -> dict[str, Any]:
        self._require_transaction()
        _text(identity, 64)
        async with self._connection.transaction():
            await self._lock_provider(definition.provider_id)
            cursor = await self._connection.execute(
                "INSERT INTO nova_meta.config_ai_models "
                "(id,provider_id,type," + ",".join(_MODEL_FIELDS) + ") VALUES ("
                + ",".join(["%s"] * (3 + len(_MODEL_FIELDS))) + ") "
                f"ON CONFLICT (id) DO NOTHING RETURNING {_MODEL_COLUMNS}",
                (identity, definition.provider_id, definition.type,
                 *(getattr(definition, name) for name in _MODEL_FIELDS)),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            return row

    async def replace_model(
        self, identity: str, definition: ModelDefinition, *, expected_revision: int,
    ) -> dict[str, Any]:
        self._require_transaction()
        _text(identity, 64)
        _revision(expected_revision)
        async with self._connection.transaction():
            await self._lock_provider(definition.provider_id)
            cursor = await self._connection.execute(
                "UPDATE nova_meta.config_ai_models SET "
                + ",".join(f"{name}=%s" for name in _MODEL_FIELDS)
                + ",metadata_revision=metadata_revision+1 WHERE id=%s AND provider_id=%s "
                "AND type=%s AND metadata_revision=%s AND NOT is_deleted "
                f"RETURNING {_MODEL_COLUMNS}",
                (*(getattr(definition, name) for name in _MODEL_FIELDS), identity,
                 definition.provider_id, definition.type, expected_revision),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            return row

    async def delete_model(
        self, identity: str, *, provider_id: str, expected_revision: int,
    ) -> None:
        self._require_transaction()
        _text(identity, 64)
        _text(provider_id, 64)
        _revision(expected_revision)
        async with self._connection.transaction():
            await self._lock_provider(provider_id)
            cursor = await self._connection.execute(
                "UPDATE nova_meta.config_ai_models SET is_deleted=true,is_active=false, "
                "metadata_revision=metadata_revision+1 "
                "WHERE id=%s AND provider_id=%s AND metadata_revision=%s AND NOT is_deleted "
                "RETURNING id", (identity, provider_id, expected_revision),
            )
            if await cursor.fetchone() is None:
                raise MetadataConflict()
