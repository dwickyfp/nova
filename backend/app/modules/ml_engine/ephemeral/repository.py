"""Durable ephemeral descriptors in NOVA_SYSTEM; local eviction cannot delete artifacts."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

import asyncmy.cursors

from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.ephemeral.cache import EphemeralEntry
from app.modules.ml_engine.spec import MLExecutionSpec
from app.modules.query.sql_pipeline import redact_for_output

EPHEMERAL_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.ML_EPHEMERAL_RUNS (
    run_id VARCHAR(64) NOT NULL,
    scope_key VARCHAR(2048) NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    descriptor_json STRING NOT NULL,
    expires_epoch DOUBLE NOT NULL
) PRIMARY KEY(run_id)
DISTRIBUTED BY HASH(run_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")
"""


def sanitized_spec(spec: MLExecutionSpec) -> dict[str, Any]:
    value = asdict(spec)
    value.pop("security")
    value.pop("budget")
    value.pop("deadline_at")
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


def encode_entry(entry: EphemeralEntry) -> str:
    output = entry.output
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
                "metrics": _sanitize(output.metrics),
                "feature_columns": output.feature_columns,
                "training_rows": output.training_rows,
                "results": output.results,
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
    def __init__(self, registry) -> None:
        self.registry = registry

    async def ensure_schema(self) -> None:
        conn = await self.registry._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(EPHEMERAL_RUNS_DDL)
        finally:
            conn.close()

    async def put(self, entry: EphemeralEntry) -> None:
        expires = time.time() + max(0, entry.expires_at - time.monotonic())
        conn = await self.registry._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO NOVA_SYSTEM.ML_EPHEMERAL_RUNS "
                    "(run_id,scope_key,fingerprint,descriptor_json,expires_epoch) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        entry.run_id,
                        entry.scope_key,
                        entry.fingerprint,
                        encode_entry(entry),
                        expires,
                    ),
                )
        finally:
            conn.close()

    async def get(self, run_id: str, scope: str) -> EphemeralEntry | None:
        rows = await self._read(
            "run_id=%s AND scope_key=%s AND expires_epoch>%s", (run_id, scope, time.time())
        )
        return rows[0] if rows else None

    async def by_fingerprint(self, fingerprint: str, scope: str) -> EphemeralEntry | None:
        rows = await self._read(
            "fingerprint=%s AND scope_key=%s AND expires_epoch>%s",
            (fingerprint, scope, time.time()),
        )
        return rows[0] if rows else None

    async def expired(self) -> list[EphemeralEntry]:
        return await self._read("expires_epoch<=%s", (time.time(),))

    async def _read(self, condition: str, params: tuple) -> list[EphemeralEntry]:
        conn = await self.registry._connect()
        try:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT descriptor_json,expires_epoch FROM NOVA_SYSTEM.ML_EPHEMERAL_RUNS "
                    f"WHERE {condition} ORDER BY expires_epoch DESC LIMIT 100",
                    params,
                )
                return [
                    decode_entry(row["descriptor_json"], row["expires_epoch"])
                    for row in await cursor.fetchall()
                ]
        finally:
            conn.close()

    async def delete_expired(self, entry: EphemeralEntry) -> None:
        conn = await self.registry._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_EPHEMERAL_RUNS "
                    "WHERE run_id=%s AND scope_key=%s AND expires_epoch<=%s",
                    (entry.run_id, entry.scope_key, time.time()),
                )
        finally:
            conn.close()

    async def remove(self, run_id: str, scope: str) -> None:
        conn = await self.registry._connect()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM NOVA_SYSTEM.ML_EPHEMERAL_RUNS WHERE run_id=%s AND scope_key=%s",
                    (run_id, scope),
                )
        finally:
            conn.close()
