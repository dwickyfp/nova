"""AI configuration commands inside the caller's PostgreSQL unit of work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg

from app.modules.ai_ml.repository import AIRegistryRepository, ModelDefinition, ProviderDefinition
from app.modules.llm_functions.registrations import FunctionRegistrationRepository
from app.modules.llm_functions.repository import AliasChange, AliasDefinition, ModelAliasRepository


@dataclass(frozen=True, slots=True)
class AIConfigurationChange:
    record: dict[str, Any] | None = field(repr=False)
    registration_jobs: tuple[dict[str, Any], ...] = field(repr=False)


class AIConfigurationService:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._registry = AIRegistryRepository(connection)
        self._aliases = ModelAliasRepository(connection)
        self._registrations = FunctionRegistrationRepository(connection)

    async def _mutate(
        self,
        operation: Callable[[], Awaitable[dict[str, Any] | AliasChange | None]],
    ) -> AIConfigurationChange:
        self._aliases._require_transaction()
        async with self._connection.transaction():
            await self._aliases.lock_mutations()
            result = await operation()
            jobs = await self._registrations.reconcile_changed()
            return AIConfigurationChange(
                result.record if isinstance(result, AliasChange) else result,
                tuple(jobs),
            )

    async def create_alias(
        self, identity: str, definition: AliasDefinition
    ) -> AIConfigurationChange:
        return await self._mutate(lambda: self._aliases.create(identity, definition))

    async def replace_alias(
        self,
        identity: str,
        definition: AliasDefinition,
        *,
        expected_revision: int,
    ) -> AIConfigurationChange:
        return await self._mutate(
            lambda: self._aliases.replace(
                identity,
                definition,
                expected_revision=expected_revision,
            )
        )

    async def delete_alias(self, identity: str, *, expected_revision: int) -> AIConfigurationChange:
        return await self._mutate(
            lambda: self._aliases.delete(identity, expected_revision=expected_revision)
        )

    async def create_provider(
        self, identity: str, definition: ProviderDefinition
    ) -> AIConfigurationChange:
        return await self._mutate(lambda: self._registry.create_provider(identity, definition))

    async def replace_provider(
        self,
        identity: str,
        definition: ProviderDefinition,
        *,
        expected_revision: int,
    ) -> AIConfigurationChange:
        return await self._mutate(
            lambda: self._registry.replace_provider(
                identity,
                definition,
                expected_revision=expected_revision,
            )
        )

    async def delete_provider(
        self, identity: str, *, expected_revision: int
    ) -> AIConfigurationChange:
        return await self._mutate(
            lambda: self._registry.delete_provider(
                identity,
                expected_revision=expected_revision,
            )
        )

    async def retire_provider(
        self, identity: str, *, expected_revision: int
    ) -> AIConfigurationChange:
        """Delete the function aliases that use a provider, then the provider.

        A referenced provider cannot be tombstoned on its own, so the API's
        cascading delete composes both in one savepoint with their intents.
        """

        async def retire() -> dict[str, Any] | None:
            while True:
                cursor = await self._connection.execute(
                    "SELECT id,metadata_revision FROM nova_meta.config_model_aliases "
                    "WHERE provider_id=%s AND NOT is_deleted ORDER BY id LIMIT 1",
                    (identity,),
                )
                alias = await cursor.fetchone()
                if alias is None:
                    break
                await self._aliases.delete(
                    alias["id"], expected_revision=alias["metadata_revision"]
                )
            await self._registry.delete_provider(identity, expected_revision=expected_revision)
            return None

        return await self._mutate(retire)

    async def create_model(
        self, identity: str, definition: ModelDefinition
    ) -> AIConfigurationChange:
        return await self._mutate(lambda: self._registry.create_model(identity, definition))

    async def replace_model(
        self,
        identity: str,
        definition: ModelDefinition,
        *,
        expected_revision: int,
    ) -> AIConfigurationChange:
        return await self._mutate(
            lambda: self._registry.replace_model(
                identity,
                definition,
                expected_revision=expected_revision,
            )
        )

    async def delete_model(
        self,
        identity: str,
        *,
        provider_id: str,
        expected_revision: int,
    ) -> AIConfigurationChange:
        return await self._mutate(
            lambda: self._registry.delete_model(
                identity,
                provider_id=provider_id,
                expected_revision=expected_revision,
            )
        )
