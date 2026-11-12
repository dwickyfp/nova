"""Model registry metadata: models, versions, aliases and run telemetry.

Rows live in PostgreSQL (``nova_meta``) and name artifacts by URI and SHA-256;
model bytes stay in the artifact store.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from app.common.metadata_actors import metadata_scope, owner_actor
from app.core.config import settings
from app.core.metadata_actor import bound_actor, current_actor
from app.core.metadata_db import MetadataConflict
from app.modules.access_control.security_context import SecurityContext
from app.modules.ml_engine.metadata_repository import MLMetadataRepository
from app.modules.ml_engine.spec import (
    MLExecutionSpec,
    MLSecurityContext,
    VersionReservationConflict,
)
from app.modules.query.sql_pipeline import redact_for_output


def security_actor(security: MLSecurityContext) -> SecurityContext:
    """The model owner's metadata actor; fails closed without one active role."""
    actor = owner_actor(
        security.username,
        security.role,
        session_id=security.session_id,
        security_context_version=security.security_context_version,
    )
    bound = bound_actor()
    if bound is not None and security.session_id is None and (
        bound.principal == actor.principal and bound.active_role == actor.active_role
        and bound.security_context_version == actor.security_context_version
        and bound.principal_type == actor.principal_type
    ):
        return bound
    return actor


class ModelRegistryRepository:
    """Model, version, alias and run metadata through the PostgreSQL repository.

    Every call takes an actor-bound transaction from the process metadata
    database. Writes run as the model owner; a reservation is serialized by the
    owner's model row lock instead of a Redis lock.
    """

    @asynccontextmanager
    async def _repository(
        self, actor: SecurityContext, *, read_only: bool = False,
    ) -> AsyncIterator[MLMetadataRepository]:
        async with metadata_scope(actor, read_only=read_only) as connection:
            yield MLMetadataRepository(connection, actor)

    def _reader(self, actor: SecurityContext | None) -> SecurityContext:
        return actor if actor is not None else current_actor()

    async def reserve_version(
        self,
        spec: MLExecutionSpec,
        *,
        feature_columns: list[str],
    ) -> tuple[str, int, str]:
        """Reserve one TRAINING version; returns its model id, version and token."""
        assert spec.model_name
        actor = security_actor(spec.security)
        try:
            async with self._repository(actor) as repository:
                reserved = await repository.reserve_version(
                    model_name=spec.model_name,
                    model_type=spec.task.value,
                    tenant=spec.security.tenant,
                    database=spec.security.database,
                    schema=spec.security.schema,
                    target_column=spec.target_column,
                    feature_columns=list(feature_columns),
                    hyperparameters=spec.parameters,
                    training_sql=spec.input_sql,
                    reservation_token=spec.reservation_token,
                )
        except MetadataConflict:
            raise VersionReservationConflict(
                "Could not reserve a model version; retry the training job"
            ) from None
        return reserved["model_id"], int(reserved["version"]), reserved["reservation_token"]

    async def register_version(
        self,
        *,
        model_id: str,
        version: int,
        reservation_token: str,
        spec: MLExecutionSpec,
        output,
        artifact_uri: str,
        artifact_sha256: str,
        artifact_size: int,
        training_duration_ms: int,
    ) -> None:
        """Publish the reserved version once, with its verified artifact provenance."""
        actor = security_actor(spec.security)
        try:
            async with self._repository(actor) as repository:
                await repository.publish_version(
                    model_id=model_id,
                    version=version,
                    reservation_token=reservation_token,
                    feature_columns=list(output.feature_columns),
                    fields={
                        "training_rows": output.training_rows,
                        "metrics": json.dumps(output.metrics, default=str),
                        "artifact_uri": artifact_uri,
                        "artifact_sha256": artifact_sha256,
                        "artifact_size": artifact_size,
                        "task": spec.task.value,
                        "framework": output.engine,
                        "framework_version": output.metrics.get("framework_version")
                        or _framework_version(output.engine),
                        "algorithm": output.algorithm,
                        "feature_schema": json.dumps(
                            output.metrics.get("feature_metadata", []), default=str
                        ),
                        "training_duration_ms": training_duration_ms,
                    },
                )
        except MetadataConflict:
            raise VersionReservationConflict(
                "Model version is no longer reserved for training"
            ) from None

    async def abort_version(
        self, model_id: str, version: int, *, actor: SecurityContext,
    ) -> bool:
        """Fail an unpublished reservation; False when the version is already published."""
        async with self._repository(actor) as repository:
            return await repository.abort_version(model_id, version)

    async def version_by_reservation(
        self, reservation_token: str, *, actor: SecurityContext | None = None,
    ) -> dict[str, Any] | None:
        async with self._repository(self._reader(actor), read_only=True) as repository:
            return await repository.version_by_reservation(reservation_token)

    async def record_run(
        self,
        *,
        run_id: str,
        spec: MLExecutionSpec,
        status: str,
        fingerprint: str,
        telemetry: dict[str, Any],
        model_id: str | None = None,
        version: int | None = None,
        artifact_uri: str | None = None,
        error: Exception | None = None,
    ) -> None:
        """Create the owner's run record once, or move a running record to its outcome."""
        async with self._repository(security_actor(spec.security)) as repository:
            await repository.record_run(
                {
                    "run_id": run_id,
                    "task": spec.task.value,
                    "mode": spec.mode.value,
                    "status": status,
                    "tenant_name": spec.security.tenant,
                    "database_name": spec.security.database,
                    "model_id": model_id,
                    "model_version": version,
                    "artifact_uri": artifact_uri,
                    "fingerprint": fingerprint,
                    "telemetry": telemetry,
                    "error_class": type(error).__name__ if error else None,
                    "error_message": redact_for_output(str(error))[:2000] if error else None,
                },
                ttl_seconds=settings.ML_EPHEMERAL_TTL_SECONDS,
            )

    async def resolve_alias(
        self,
        alias: str,
        *,
        owner_name: str,
        database_name: str | None,
        tenant: str = "default",
        actor: SecurityContext | None = None,
    ) -> dict[str, Any] | None:
        async with self._repository(self._reader(actor), read_only=True) as repository:
            row = await repository.resolve_alias(
                alias, owner_name=owner_name, database_name=database_name, tenant=tenant
            )
            return dict(row) if row else None

    async def get_version(
        self,
        model_id: str,
        version: int,
        *,
        owner_name: str,
        database_name: str | None,
        tenant: str = "default",
        actor: SecurityContext | None = None,
    ) -> dict[str, Any] | None:
        async with self._repository(self._reader(actor), read_only=True) as repository:
            row = await repository.get_version(
                model_id,
                version,
                owner_name=owner_name,
                database_name=database_name,
                tenant=tenant,
            )
            return dict(row) if row else None

    async def set_alias(
        self,
        alias: str,
        model_id: str,
        version: int,
        *,
        actor: SecurityContext,
        database_name: str | None,
        tenant: str = "default",
    ) -> dict[str, Any]:
        """Create or move the owner's alias to a published version of their model."""
        owner_name = actor.principal
        try:
            async with self._repository(actor) as repository:
                target = await repository.get_version(
                    model_id,
                    version,
                    owner_name=owner_name,
                    database_name=database_name,
                    tenant=tenant,
                )
                if target is None:
                    raise ValueError("Model version not found")
                current = await repository.get_alias(
                    alias, database_name=database_name, tenant=tenant
                )
                await repository.set_alias(
                    alias,
                    model_id,
                    version,
                    database_name=database_name,
                    tenant=tenant,
                    expected_revision=(
                        int(current["metadata_revision"]) if current is not None else None
                    ),
                )
        except MetadataConflict:
            raise ValueError("The alias changed while it was being updated; retry") from None
        return {
            "alias_name": alias,
            "model_id": model_id,
            "version": version,
            "owner_name": owner_name,
            "database_name": database_name or "",
        }

    async def delete_alias(
        self,
        alias: str,
        *,
        actor: SecurityContext,
        database_name: str | None,
        tenant: str = "default",
    ) -> bool:
        async with self._repository(actor) as repository:
            return await repository.delete_alias(
                alias, database_name=database_name, tenant=tenant
            )

    async def list_aliases(
        self,
        *,
        owner_name: str,
        database_name: str | None,
        tenant: str = "default",
        actor: SecurityContext | None = None,
    ) -> list[dict[str, Any]]:
        async with self._repository(self._reader(actor), read_only=True) as repository:
            return await repository.list_aliases(
                owner_name=owner_name, database_name=database_name, tenant=tenant
            )

    async def list_models(
        self,
        *,
        owner_name: str,
        database_name: str | None,
        tenant: str = "default",
        actor: SecurityContext | None = None,
    ) -> list[dict[str, Any]]:
        async with self._repository(self._reader(actor), read_only=True) as repository:
            return await repository.list_models(
                owner_name=owner_name, database_name=database_name, tenant=tenant
            )

    async def list_versions(
        self, model_id: str, *, actor: SecurityContext | None = None,
    ) -> list[dict[str, Any]]:
        async with self._repository(self._reader(actor), read_only=True) as repository:
            return await repository.list_versions(model_id)

    async def delete_model(self, model_id: str, *, actor: SecurityContext) -> bool:
        """Delete the owner's model, its aliases and versions in one transaction."""
        async with self._repository(actor) as repository:
            return await repository.delete_model(model_id)


model_registry_repository = ModelRegistryRepository()


def alias_database_scope(database_name: str | None, tenant: str) -> str:
    if tenant == "default":
        return database_name or ""
    value = json.dumps([tenant, database_name or ""], separators=(",", ":"))
    return "tenant:" + hashlib.sha256(value.encode()).hexdigest()


def _framework_version(engine: str) -> str:
    distributions = {
        "flaml": "FLAML",
        "statsforecast": "statsforecast",
        "pyod": "pyod",
        "sklearn": "scikit-learn",
        "scikit_learn_clustering": "scikit-learn",
    }
    try:
        return package_version(distributions.get(engine, engine))
    except PackageNotFoundError:
        return "unknown"
