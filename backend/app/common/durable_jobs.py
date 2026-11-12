"""Transactional job storage. Participant adapters own external outcome proof.

Commit PREPARED before external I/O. A recovered lease can verify an armed
operation, but cannot prepare or submit it again. Redis carries wakeups only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4, uuid5

import psycopg
from psycopg.pq import TransactionStatus

from app.core.metadata_db import MetadataConflict

QUEUES = frozenset(
    {
        "query",
        "agent",
        "task",
        "ingestion",
        "ml",
        "notebook",
        "automation",
        "maintenance",
    }
)


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", value):
        raise ValueError("Invalid durable job identifier")


def _digest(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Invalid durable job digest")


def _integer(value: int, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Invalid durable job bound")


def _uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise ValueError("Durable job references must be UUIDs")


@dataclass(frozen=True, slots=True)
class JobDefinition:
    queue: str
    kind: str
    definition_ref: UUID
    definition_version: int
    definition_digest: str
    idempotency_key: UUID
    resource_key: str
    external_effect: bool

    def __post_init__(self) -> None:
        if self.queue not in QUEUES or type(self.external_effect) is not bool:
            raise ValueError("Invalid durable job workload")
        _identifier(self.kind)
        _uuid(self.definition_ref)
        _uuid(self.idempotency_key)
        _integer(self.definition_version, 2**63 - 1)
        _digest(self.definition_digest)
        _digest(self.resource_key)


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: UUID
    holder: UUID
    token: int
    revision: int

    def __post_init__(self) -> None:
        _uuid(self.job_id)
        _uuid(self.holder)
        _integer(self.token, 2**63 - 1)
        _integer(self.revision, 2**63 - 1)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> JobLease:
        return cls(record["id"], record["lease_holder"], record["lease_token"], record["revision"])


@dataclass(frozen=True, slots=True)
class EffectProof:
    """Reference to participant evidence, never a raw response or credentials."""

    evidence_ref: UUID
    result_digest: str

    def __post_init__(self) -> None:
        _uuid(self.evidence_ref)
        _digest(self.result_digest)


class JobRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Durable jobs require an active metadata transaction")

    async def get(self, job_id: UUID) -> dict[str, Any] | None:
        self._require_transaction()
        _uuid(job_id)
        cursor = await self._connection.execute(
            "SELECT * FROM nova_meta.job_queue WHERE id=%s",
            (job_id,),
        )
        return await cursor.fetchone()

    async def enqueue(self, definition: JobDefinition) -> dict[str, Any]:
        self._require_transaction()
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                "INSERT INTO nova_meta.job_queue "
                "(id, queue, kind, definition_ref, definition_version, definition_digest, "
                "idempotency_key, resource_key, external_effect) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (idempotency_key) DO NOTHING RETURNING *",
                (
                    uuid4(),
                    definition.queue,
                    definition.kind,
                    definition.definition_ref,
                    definition.definition_version,
                    definition.definition_digest,
                    definition.idempotency_key,
                    definition.resource_key,
                    definition.external_effect,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                cursor = await self._connection.execute(
                    "SELECT * FROM nova_meta.job_queue WHERE idempotency_key=%s "
                    "AND owner_principal=current_setting('nova.principal') "
                    "AND owner_role=current_setting('nova.active_role') "
                    "AND principal_type=current_setting('nova.principal_type') "
                    "AND security_context_version="
                    "current_setting('nova.security_context_version')::bigint",
                    (definition.idempotency_key,),
                )
                row = await cursor.fetchone()
                if row is None or any(
                    row[name] != getattr(definition, name)
                    for name in (
                        "queue",
                        "kind",
                        "definition_ref",
                        "definition_version",
                        "definition_digest",
                        "idempotency_key",
                        "resource_key",
                        "external_effect",
                    )
                ):
                    raise MetadataConflict()
                return row
            await self._wake(row)
            return row

    async def _wake(self, row: dict[str, Any]) -> None:
        await self._connection.execute(
            "INSERT INTO nova_meta.outbox_events (id,job_id,ready_revision) VALUES (%s,%s,%s)",
            (uuid4(), row["id"], row["revision"]),
        )

    async def claim(
        self,
        queue: str,
        holder: UUID,
        *,
        seconds: int = 60,
        limit: int = 1,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_transaction()
        if queue not in QUEUES:
            raise ValueError("Invalid durable job queue")
        _uuid(holder)
        _integer(seconds, 300)
        _integer(limit, 100)
        if kind is not None:
            _identifier(kind)
        kind_clause = " AND kind=%s" if kind is not None else ""
        cursor = await self._connection.execute(
            "WITH due AS (SELECT id FROM nova_meta.job_queue WHERE queue=%s" + kind_clause + " AND "
            "nova_meta.job_worker(queue) AND ((state='PENDING' "
            "AND next_attempt_at<=clock_timestamp()) "
            "OR (state IN ('LEASED','PREPARED','SUBMITTED','VERIFYING','BLOCKED','COMMITTED') "
            "AND lease_expires_at<=clock_timestamp())) "
            "ORDER BY next_attempt_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT %s) "
            "UPDATE nova_meta.job_queue j SET "
            "state=CASE WHEN state IN ('PENDING','LEASED') THEN 'LEASED' "
            "WHEN state='COMMITTED' THEN 'COMMITTED' ELSE 'VERIFYING' END, "
            "attempt=attempt+CASE WHEN state IN ('PENDING','LEASED') THEN 1 ELSE 0 END, "
            "revision=revision+1, lease_holder=%s, lease_token=lease_token+1, "
            "lease_expires_at=clock_timestamp()+%s*interval '1 second' "
            "FROM due WHERE j.id=due.id RETURNING j.*",
            ((queue, kind, limit, holder, seconds) if kind is not None
             else (queue, limit, holder, seconds)),
        )
        return list(await cursor.fetchall())

    async def _locked(self, lease: JobLease, states: tuple[str, ...]) -> dict[str, Any]:
        self._require_transaction()
        cursor = await self._connection.execute(
            "SELECT * FROM nova_meta.job_queue WHERE id=%s AND lease_holder=%s "
            "AND lease_token=%s AND revision=%s AND state=ANY(%s) "
            "AND lease_expires_at>clock_timestamp() AND nova_meta.job_worker(queue) FOR UPDATE",
            (lease.job_id, lease.holder, lease.token, lease.revision, list(states)),
        )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return row

    async def _state(
        self,
        job_id: UUID,
        state: str,
        *,
        error: str | None = None,
        retryable: bool = False,
    ) -> dict[str, Any]:
        cursor = await self._connection.execute(
            "UPDATE nova_meta.job_queue SET state=%s,revision=revision+1,"
            "error_class=%s,retryable=%s "
            "WHERE id=%s RETURNING *",
            (state, error, retryable, job_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise MetadataConflict()
        return row

    async def heartbeat(self, lease: JobLease, *, seconds: int = 60) -> dict[str, Any]:
        _integer(seconds, 300)
        self._require_transaction()
        async with self._connection.transaction():
            await self._locked(lease, ("LEASED", "PREPARED", "SUBMITTED", "VERIFYING", "COMMITTED"))
            cursor = await self._connection.execute(
                "UPDATE nova_meta.job_queue SET revision=revision+1, "
                "lease_expires_at=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING *",
                (seconds, lease.job_id),
            )
            row = await cursor.fetchone()
            assert row is not None
            return row

    async def lock_prepared(self, lease: JobLease) -> dict[str, Any]:
        """Fence participant intent/receipt writes inside their owning transaction."""
        self._require_transaction()
        return await self._locked(lease, ("PREPARED",))

    async def prepare(
        self,
        lease: JobLease,
        *,
        participant: str,
        request_digest: str,
    ) -> dict[str, Any]:
        _identifier(participant)
        _digest(request_digest)
        self._require_transaction()
        async with self._connection.transaction():
            job = await self._locked(lease, ("LEASED",))
            if not job["external_effect"]:
                raise MetadataConflict()
            row = await self._state(lease.job_id, "PREPARED")
            await self._connection.execute(
                "INSERT INTO nova_meta.external_effect_intents "
                "(job_id,attempt,operation_id,participant,request_digest) VALUES (%s,%s,%s,%s,%s)",
                (
                    job["id"],
                    job["attempt"],
                    uuid5(job["id"], str(job["attempt"])),
                    participant,
                    request_digest,
                ),
            )
            return row

    async def intent(self, job_id: UUID, attempt: int) -> dict[str, Any] | None:
        self._require_transaction()
        _uuid(job_id)
        _integer(attempt, 2**31 - 1)
        cursor = await self._connection.execute(
            "SELECT * FROM nova_meta.external_effect_intents WHERE job_id=%s AND attempt=%s",
            (job_id, attempt),
        )
        return await cursor.fetchone()

    async def submitted(self, lease: JobLease) -> dict[str, Any]:
        self._require_transaction()
        async with self._connection.transaction():
            await self._locked(lease, ("PREPARED",))
            return await self._state(lease.job_id, "SUBMITTED")

    async def verifying(self, lease: JobLease) -> dict[str, Any]:
        self._require_transaction()
        async with self._connection.transaction():
            await self._locked(lease, ("PREPARED", "SUBMITTED", "BLOCKED"))
            return await self._state(lease.job_id, "VERIFYING")

    async def block(self, lease: JobLease, *, error: str) -> dict[str, Any]:
        self._error(error)
        self._require_transaction()
        async with self._connection.transaction():
            await self._locked(lease, ("VERIFYING",))
            return await self._state(lease.job_id, "BLOCKED", error=error)

    @staticmethod
    def _error(error: str | None) -> None:
        if not isinstance(error, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error):
            raise ValueError("Job failures require a redacted error classification")

    async def record_outcome(
        self,
        lease: JobLease,
        proof: EffectProof,
        *,
        visible: bool,
        error: str | None = None,
        retryable: bool = False,
    ) -> dict[str, Any]:
        if type(visible) is not bool or type(retryable) is not bool:
            raise ValueError("Invalid external effect outcome")
        if visible and (error is not None or retryable):
            raise ValueError("A visible effect cannot be retried")
        if not visible:
            self._error(error)
        self._require_transaction()
        async with self._connection.transaction():
            job = await self._locked(lease, ("VERIFYING",))
            await self._connection.execute(
                "INSERT INTO nova_meta.external_effect_receipts "
                "(job_id,attempt,state,evidence_ref,result_digest) VALUES (%s,%s,%s,%s,%s)",
                (
                    job["id"],
                    job["attempt"],
                    "VISIBLE" if visible else "ABSENT",
                    proof.evidence_ref,
                    proof.result_digest,
                ),
            )
            return await self._state(
                job["id"],
                "COMMITTED" if visible else "FAILED",
                error=error,
                retryable=retryable,
            )

    async def commit_internal(self, lease: JobLease) -> dict[str, Any]:
        self._require_transaction()
        async with self._connection.transaction():
            job = await self._locked(lease, ("LEASED",))
            if job["external_effect"]:
                raise MetadataConflict()
            return await self._state(job["id"], "COMMITTED")

    async def fail_internal(
        self,
        lease: JobLease,
        *,
        error: str,
        retryable: bool = False,
    ) -> dict[str, Any]:
        self._require_transaction()
        self._error(error)
        if type(retryable) is not bool:
            raise ValueError("Invalid internal job outcome")
        async with self._connection.transaction():
            job = await self._locked(lease, ("LEASED",))
            if job["external_effect"]:
                raise MetadataConflict()
            return await self._state(job["id"], "FAILED", error=error, retryable=retryable)

    async def cancel_unprepared(self, lease: JobLease) -> dict[str, Any]:
        self._require_transaction()
        async with self._connection.transaction():
            await self._locked(lease, ("LEASED",))
            return await self._state(lease.job_id, "CANCELLED")

    async def complete(self, lease: JobLease) -> dict[str, Any]:
        self._require_transaction()
        async with self._connection.transaction():
            await self._locked(lease, ("COMMITTED",))
            return await self._state(lease.job_id, "COMPLETED")

    async def retry(
        self,
        job_id: UUID,
        revision: int,
        *,
        delay_seconds: int,
    ) -> dict[str, Any]:
        self._require_transaction()
        _uuid(job_id)
        _integer(revision, 2**63 - 1)
        _integer(delay_seconds, 86400)
        self._require_transaction()
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                "UPDATE nova_meta.job_queue SET state='PENDING',revision=revision+1, "
                "lease_holder=NULL,lease_expires_at=NULL,retryable=false, "
                "next_attempt_at=clock_timestamp()+%s*interval '1 second' "
                "WHERE id=%s AND revision=%s AND state='FAILED' AND retryable "
                "AND nova_meta.job_worker(queue) RETURNING *",
                (delay_seconds, job_id, revision),
            )
            row = await cursor.fetchone()
            if row is None:
                raise MetadataConflict()
            await self._wake(row)
            return row


class OutboxRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Outbox requires an active metadata transaction")

    async def claim(
        self,
        holder: UUID,
        *,
        seconds: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._require_transaction()
        _uuid(holder)
        _integer(seconds, 300)
        _integer(limit, 100)
        cursor = await self._connection.execute(
            "WITH due AS (SELECT id FROM nova_meta.outbox_events WHERE published_at IS NULL "
            "AND next_publish_at<=clock_timestamp() "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp()) "
            "ORDER BY next_publish_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT %s) "
            "UPDATE nova_meta.outbox_events e SET lease_holder=%s,lease_token=lease_token+1, "
            "lease_expires_at=clock_timestamp()+%s*interval '1 second', "
            "publish_attempt=publish_attempt+1 FROM due WHERE e.id=due.id RETURNING e.*",
            (limit, holder, seconds),
        )
        return list(await cursor.fetchall())

    async def delivered(self, event: dict[str, Any]) -> None:
        self._require_transaction()
        cursor = await self._connection.execute(
            "UPDATE nova_meta.outbox_events SET published_at=clock_timestamp() "
            "WHERE id=%s AND lease_holder=%s AND lease_token=%s "
            "AND lease_expires_at>clock_timestamp() AND published_at IS NULL RETURNING id",
            (event["id"], event["lease_holder"], event["lease_token"]),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()

    async def defer(self, event: dict[str, Any], *, delay_seconds: int) -> None:
        self._require_transaction()
        _integer(delay_seconds, 3600)
        cursor = await self._connection.execute(
            "UPDATE nova_meta.outbox_events "
            "SET next_publish_at=clock_timestamp()+%s*interval '1 second', "
            "lease_holder=NULL,lease_expires_at=NULL "
            "WHERE id=%s AND lease_holder=%s AND lease_token=%s "
            "AND lease_expires_at>clock_timestamp() AND published_at IS NULL RETURNING id",
            (delay_seconds, event["id"], event["lease_holder"], event["lease_token"]),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()

    @staticmethod
    def notification(event: dict[str, Any]) -> dict[str, str]:
        return {"event_id": str(event["id"]), "job_id": str(event["job_id"])}
