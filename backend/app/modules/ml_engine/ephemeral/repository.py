"""Durable ephemeral descriptors in PostgreSQL; local eviction cannot delete artifacts."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from app.common.metadata_actors import metadata_scope, service_actor
from app.modules.access_control.security_context import SecurityContext
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry
from app.modules.ml_engine.metadata_repository import MLMetadataRepository
from app.modules.ml_engine.spec import MLExecutionSpec
from app.modules.query.sql_pipeline import redact_for_output


def sanitized_spec(spec: MLExecutionSpec) -> dict[str, Any]:
    value = asdict(spec)
    value.pop("security")
    value.pop("budget")
    value.pop("deadline_at")
    value.pop("reservation_token", None)
    value["input_sql"] = redact_for_output(spec.input_sql)
    value["parameters"] = _sanitize(value["parameters"])
    return value


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if not any(
                word in key.casefold()
                for word in ("password", "secret", "credential", "token", "access_key")
            )
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    return redact_for_output(value) if isinstance(value, str) else value


#: The descriptor column holds 65,533 characters; the descriptor stays under it
#: with room for multi-byte text.
MAX_DESCRIPTOR_BYTES = 60_000


def encode_entry(entry: EphemeralEntry) -> str:
    """The cached run as JSON that fits its column: the result preview is trimmed
    from the end (and marked) when the whole of it would not."""
    results = list(entry.output.results)
    encoded = _encode(entry, results, truncated=False)
    if len(encoded.encode()) <= MAX_DESCRIPTOR_BYTES:
        return encoded
    low, high = 0, len(results)
    while low < high:
        middle = (low + high + 1) // 2
        size = len(_encode(entry, results[:middle], truncated=True).encode())
        low, high = (middle, high) if size <= MAX_DESCRIPTOR_BYTES else (low, middle - 1)
    return _encode(entry, results[:low], truncated=True)


def _encode(entry: EphemeralEntry, results: list[dict[str, Any]], *, truncated: bool) -> str:
    output = entry.output
    metrics = _sanitize(output.metrics)
    if truncated:
        metrics = {**metrics, "cached_results_truncated": True}
    return json.dumps(
        {
            "run_id": entry.run_id,
            "scope_key": entry.scope_key,
            "fingerprint": entry.fingerprint,
            "task": entry.task,
            "artifact_uri": entry.artifact_uri,
            "artifact_sha256": entry.artifact_sha256,
            "artifact_size": entry.artifact_size,
            "artifact_kind": entry.artifact_kind,
            "spec_snapshot": entry.spec_snapshot,
            "output": {
                "engine": output.engine,
                "algorithm": output.algorithm,
                "metrics": metrics,
                "feature_columns": output.feature_columns,
                "training_rows": output.training_rows,
                "results": results,
            },
        },
        default=str,
    )


def decode_entry(payload: str, expires_epoch: float) -> EphemeralEntry:
    value = json.loads(payload)
    value["output"] = TrainingOutput(bundle={}, **value["output"])
    return EphemeralEntry(
        **value, expires_at=time.monotonic() + max(0, expires_epoch - time.time())
    )


class EphemeralRepository:
    """Ephemeral run descriptors, each read or written in an actor-bound transaction.

    Callers pass the run owner's actor; the retention sweep passes the
    maintenance service actor, which may delete only expired descriptors.
    """

    @asynccontextmanager
    async def _repository(
        self, actor: SecurityContext | None, *, read_only: bool = False,
    ) -> AsyncIterator[MLMetadataRepository]:
        bound = actor or service_actor("ml")
        async with metadata_scope(bound, read_only=read_only) as connection:
            yield MLMetadataRepository(connection, bound)

    async def put(self, entry: EphemeralEntry, *, actor: SecurityContext | None = None) -> None:
        expires = time.time() + max(0, entry.expires_at - time.monotonic())
        async with self._repository(actor) as repository:
            await repository.put_ephemeral(
                run_id=entry.run_id,
                scope_key=entry.scope_key,
                fingerprint=entry.fingerprint,
                descriptor_json=encode_entry(entry),
                expires_epoch=expires,
            )

    async def get(
        self, run_id: str, scope: str, *, actor: SecurityContext | None = None,
    ) -> EphemeralEntry | None:
        async with self._repository(actor, read_only=True) as repository:
            row = await repository.get_ephemeral(run_id, scope)
        return decode_entry(row["descriptor_json"], row["expires_epoch"]) if row else None

    async def by_fingerprint(
        self, fingerprint: str, scope: str, *, actor: SecurityContext | None = None,
    ) -> EphemeralEntry | None:
        async with self._repository(actor, read_only=True) as repository:
            row = await repository.ephemeral_by_fingerprint(fingerprint, scope)
        return decode_entry(row["descriptor_json"], row["expires_epoch"]) if row else None

    async def expired(self, *, actor: SecurityContext | None = None) -> list[EphemeralEntry]:
        async with self._repository(actor, read_only=True) as repository:
            rows = await repository.expired_ephemeral(limit=100)
        return [decode_entry(row["descriptor_json"], row["expires_epoch"]) for row in rows]

    async def delete_expired(
        self, entry: EphemeralEntry, *, actor: SecurityContext | None = None,
    ) -> None:
        async with self._repository(actor) as repository:
            await repository.delete_expired_ephemeral(entry.run_id, entry.scope_key)

    async def remove(
        self, run_id: str, scope: str, *, actor: SecurityContext | None = None,
    ) -> None:
        async with self._repository(actor) as repository:
            await repository.remove_ephemeral(run_id, scope)
