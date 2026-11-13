"""Transactional function-alias storage for the PostgreSQL cutover."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from app.core.metadata_db import MetadataConflict
from app.modules.ai_ml.repository import _parameters, _revision, _text, lock_ai_configuration
from app.modules.llm_functions.schemas import VALID_FUNCTION_TYPES


@dataclass(frozen=True, slots=True)
class AliasDefinition:
    alias_name: str
    function_type: str
    provider_id: str
    model_id: str | None = None
    system_prompt: str | None = field(default=None, repr=False)
    default_params: str | None = field(default=None, repr=False)
    is_default: bool = True
    is_active: bool = True

    def __post_init__(self) -> None:
        _text(self.alias_name, 128)
        _text(self.function_type, 32)
        _text(self.provider_id, 64)
        if self.function_type not in VALID_FUNCTION_TYPES:
            raise ValueError("Invalid alias function type")
        if self.model_id is not None:
            _text(self.model_id, 64)
        if self.system_prompt is not None:
            _text(self.system_prompt, 65533, empty=True)
        _parameters(self.default_params)
        if type(self.is_default) is not bool or type(self.is_active) is not bool:
            raise ValueError("Invalid alias state")


@dataclass(frozen=True, slots=True)
class AliasChange:
    record: dict[str, Any] = field(repr=False)
    affected_function_types: tuple[str, ...]


_FIELDS = (
    "alias_name",
    "function_type",
    "provider_id",
    "model_id",
    "system_prompt",
    "default_params",
    "is_default",
    "is_active",
)
_COLUMNS = (
    "id," + ",".join(_FIELDS) + (",created_at,updated_at,created_by,metadata_revision,is_deleted")
)


class ModelAliasRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Model aliases require an active metadata transaction")

    async def aliases(self, function_type: str | None = None) -> list[dict[str, Any]]:
        self._require_transaction()
        if function_type is not None:
            self._validate_type(function_type)
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM nova_meta.config_model_aliases WHERE NOT is_deleted "
            "AND (%s::text IS NULL OR function_type=%s) ORDER BY function_type,alias_name,id",
            (function_type, function_type),
        )
        return await cursor.fetchall()

    async def alias(self, identity: str) -> dict[str, Any] | None:
        self._require_transaction()
        _text(identity, 64)
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM nova_meta.config_model_aliases WHERE id=%s",
            (identity,),
        )
        return await cursor.fetchone()

    @staticmethod
    def _validate_type(function_type: str) -> None:
        _text(function_type, 32)
        if function_type not in VALID_FUNCTION_TYPES:
            raise ValueError("Invalid alias function type")

    async def selected_alias(self, function_type: str) -> dict[str, Any] | None:
        self._require_transaction()
        self._validate_type(function_type)
        # An inactive configured default must not silently select another provider.
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM nova_meta.config_model_aliases WHERE function_type=%s "
            "AND NOT is_deleted AND (is_default IS TRUE OR is_active IS TRUE) "
            "ORDER BY (is_default IS TRUE) DESC,alias_name,id LIMIT 1",
            (function_type,),
        )
        return await cursor.fetchone()

    async def lock_mutations(self) -> None:
        self._require_transaction()
        await lock_ai_configuration(self._connection)

    async def _lock_definition(self, definition: AliasDefinition) -> None:
        cursor = await self._connection.execute(
            "SELECT id FROM nova_meta.config_ai_providers "
            "WHERE id=%s AND NOT is_deleted FOR UPDATE",
            (definition.provider_id,),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()
        if definition.model_id is not None:
            cursor = await self._connection.execute(
                "SELECT id FROM nova_meta.config_ai_models WHERE id=%s AND provider_id=%s "
                "AND type=%s AND NOT is_deleted FOR SHARE",
                (
                    definition.model_id,
                    definition.provider_id,
                    "embedding" if definition.function_type == "embed" else "llm",
                ),
            )
            if await cursor.fetchone() is None:
                raise MetadataConflict()

    async def _existing(self, identity: str, revision: int) -> dict[str, Any]:
        cursor = await self._connection.execute(
            f"SELECT {_COLUMNS} FROM nova_meta.config_model_aliases "
            "WHERE id=%s AND metadata_revision=%s AND NOT is_deleted FOR UPDATE",
            (identity, revision),
        )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return row

    async def _clear_default(self, function_type: str, identity: str) -> None:
        await self._connection.execute(
            "UPDATE nova_meta.config_model_aliases SET is_default=false, "
            "metadata_revision=metadata_revision+1 WHERE function_type=%s AND id<>%s "
            "AND is_default IS TRUE AND NOT is_deleted",
            (function_type, identity),
        )

    async def create(self, identity: str, definition: AliasDefinition) -> AliasChange:
        self._require_transaction()
        _text(identity, 64)
        async with self._connection.transaction():
            await self.lock_mutations()
            await self._lock_definition(definition)
            if definition.is_default:
                await self._clear_default(definition.function_type, identity)
            cursor = await self._connection.execute(
                "INSERT INTO nova_meta.config_model_aliases (id," + ",".join(_FIELDS) + ") "
                "VALUES (" + ",".join(["%s"] * (len(_FIELDS) + 1)) + ") "
                f"ON CONFLICT (id) DO NOTHING RETURNING {_COLUMNS}",
                (identity, *(getattr(definition, name) for name in _FIELDS)),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            return AliasChange(row, (definition.function_type,))

    async def replace(
        self,
        identity: str,
        definition: AliasDefinition,
        *,
        expected_revision: int,
    ) -> AliasChange:
        self._require_transaction()
        _text(identity, 64)
        _revision(expected_revision)
        async with self._connection.transaction():
            await self.lock_mutations()
            previous = await self._existing(identity, expected_revision)
            await self._lock_definition(definition)
            if definition.is_default:
                await self._clear_default(definition.function_type, identity)
            cursor = await self._connection.execute(
                "UPDATE nova_meta.config_model_aliases SET "
                + ",".join(f"{name}=%s" for name in _FIELDS)
                + f",metadata_revision=metadata_revision+1 WHERE id=%s RETURNING {_COLUMNS}",
                (*(getattr(definition, name) for name in _FIELDS), identity),
            )
            row = await cursor.fetchone()
            assert row is not None
            return AliasChange(
                row, tuple(sorted({previous["function_type"], definition.function_type}))
            )

    async def delete(self, identity: str, *, expected_revision: int) -> AliasChange:
        self._require_transaction()
        _text(identity, 64)
        _revision(expected_revision)
        async with self._connection.transaction():
            await self.lock_mutations()
            previous = await self._existing(identity, expected_revision)
            cursor = await self._connection.execute(
                "UPDATE nova_meta.config_model_aliases SET is_deleted=true,is_default=false, "
                "is_active=false,metadata_revision=metadata_revision+1 "
                f"WHERE id=%s RETURNING {_COLUMNS}",
                (identity,),
            )
            row = await cursor.fetchone()
            assert row is not None
            if previous["is_default"]:
                await self._connection.execute(
                    "UPDATE nova_meta.config_model_aliases SET is_default=true, "
                    "metadata_revision=metadata_revision+1 WHERE id=(SELECT id "
                    "FROM nova_meta.config_model_aliases WHERE function_type=%s AND NOT is_deleted "
                    "AND is_active IS TRUE ORDER BY alias_name,id LIMIT 1)",
                    (previous["function_type"],),
                )
            return AliasChange(row, (previous["function_type"],))
