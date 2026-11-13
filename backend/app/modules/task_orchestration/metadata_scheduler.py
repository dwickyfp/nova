"""PG leadership and occurrence reservation for the Task metadata cutover."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.pq import TransactionStatus

from app.common.durable_jobs import JobDefinition, JobRepository
from app.core.metadata_db import MetadataConflict


def _name(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", value):
        raise ValueError("Invalid scheduler lease name")


def _duration(seconds: int) -> None:
    if type(seconds) is not int or not 1 <= seconds <= 300:
        raise ValueError("Invalid scheduler lease duration")


def _object_id(value: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or "\x00" in value:
        raise ValueError("Invalid scheduled Task identity")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid scheduled Task identity") from None


@dataclass(frozen=True, slots=True)
class SchedulerLease:
    name: str
    holder: UUID
    token: int

    def __post_init__(self) -> None:
        _name(self.name)
        if (
            not isinstance(self.holder, UUID)
            or type(self.token) is not int
            or not 1 <= self.token < 2**63
        ):
            raise ValueError("Invalid scheduler lease")


@dataclass(frozen=True, slots=True)
class ScheduledOccurrence:
    root_task_id: str
    scheduled_at: datetime
    graph_run_id: str
    definition: JobDefinition

    def __post_init__(self) -> None:
        _object_id(self.root_task_id)
        _object_id(self.graph_run_id)
        if not isinstance(self.scheduled_at, datetime) or self.scheduled_at.utcoffset() is None:
            raise ValueError("Schedule occurrences require an explicit timezone")
        if (
            self.definition.queue != "task"
            or self.definition.kind != "task.graph"
            or self.definition.external_effect
        ):
            raise ValueError("A scheduled graph orchestrates separately fenced node jobs")

    @property
    def idempotency_key(self) -> UUID:
        identity = json.dumps(
            [
                self.root_task_id,
                self.definition.definition_version,
                self.scheduled_at.astimezone(UTC).isoformat(timespec="microseconds"),
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return uuid5(NAMESPACE_URL, "nova:scheduled-task:v1:" + identity)


class SchedulerRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Scheduler storage requires an active metadata transaction")

    async def acquire(
        self,
        name: str,
        holder: UUID,
        *,
        seconds: int = 60,
    ) -> SchedulerLease | None:
        self._require_transaction()
        _name(name)
        _duration(seconds)
        if not isinstance(holder, UUID):
            raise ValueError("Invalid scheduler holder")
        cursor = await self._connection.execute(
            "INSERT INTO nova_meta.scheduler_leases (name,holder,token,expires_at) "
            "VALUES (%s,%s,1,clock_timestamp()+%s*interval '1 second') "
            "ON CONFLICT (name) DO UPDATE SET holder=excluded.holder, "
            "token=scheduler_leases.token+1,expires_at=excluded.expires_at "
            "WHERE scheduler_leases.expires_at<=clock_timestamp() RETURNING name,holder,token",
            (name, holder, seconds),
        )
        row = await cursor.fetchone()
        return SchedulerLease(**row) if row else None

    async def renew(self, lease: SchedulerLease, *, seconds: int = 60) -> None:
        self._require_transaction()
        _duration(seconds)
        cursor = await self._connection.execute(
            "UPDATE nova_meta.scheduler_leases "
            "SET expires_at=clock_timestamp()+%s*interval '1 second' "
            "WHERE name=%s AND holder=%s AND token=%s "
            "AND expires_at>clock_timestamp() RETURNING token",
            (seconds, lease.name, lease.holder, lease.token),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()

    async def release(self, lease: SchedulerLease) -> None:
        self._require_transaction()
        cursor = await self._connection.execute(
            "UPDATE nova_meta.scheduler_leases SET expires_at=clock_timestamp() "
            "WHERE name=%s AND holder=%s AND token=%s "
            "AND expires_at>clock_timestamp() RETURNING token",
            (lease.name, lease.holder, lease.token),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()

    async def _lock_leader(self, lease: SchedulerLease) -> None:
        cursor = await self._connection.execute(
            "SELECT token FROM nova_meta.scheduler_leases WHERE name=%s AND holder=%s AND token=%s "
            "AND expires_at>clock_timestamp() FOR SHARE",
            (lease.name, lease.holder, lease.token),
        )
        if await cursor.fetchone() is None:
            raise MetadataConflict()

    async def reserve(
        self,
        lease: SchedulerLease,
        occurrence: ScheduledOccurrence,
    ) -> dict[str, Any]:
        """Reserve only a due occurrence, in the graph owner's domain transaction.

        The owning service must persist the matching graph-run row in this same
        transaction. WHEN evaluation and node execution belong to the graph
        runtime, not to this clock/leadership repository.
        """
        self._require_transaction()
        async with self._connection.transaction():
            await self._lock_leader(lease)
            cursor = await self._connection.execute(
                "SELECT %s::timestamptz<=clock_timestamp() AS due",
                (occurrence.scheduled_at,),
            )
            due = await cursor.fetchone()
            if not due or not due["due"]:
                raise MetadataConflict()
            definition = replace(occurrence.definition, idempotency_key=occurrence.idempotency_key)
            job = await JobRepository(self._connection).enqueue(definition)
            cursor = await self._connection.execute(
                "INSERT INTO nova_meta.scheduled_occurrences "
                "(root_task_id,definition_version,scheduled_at,definition_ref,definition_digest,"
                "graph_run_id,job_id,lease_name,lease_holder,lease_token) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (root_task_id,definition_version,scheduled_at) DO NOTHING RETURNING *",
                (
                    occurrence.root_task_id,
                    definition.definition_version,
                    occurrence.scheduled_at,
                    definition.definition_ref,
                    definition.definition_digest,
                    occurrence.graph_run_id,
                    job["id"],
                    lease.name,
                    lease.holder,
                    lease.token,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                cursor = await self._connection.execute(
                    "SELECT * FROM nova_meta.scheduled_occurrences "
                    "WHERE root_task_id=%s AND definition_version=%s AND scheduled_at=%s",
                    (
                        occurrence.root_task_id,
                        definition.definition_version,
                        occurrence.scheduled_at,
                    ),
                )
                row = await cursor.fetchone()
                if row is None or any(
                    row[key] != value
                    for key, value in (
                        ("definition_ref", definition.definition_ref),
                        ("definition_digest", definition.definition_digest),
                        ("graph_run_id", occurrence.graph_run_id),
                        ("job_id", job["id"]),
                    )
                ):
                    raise MetadataConflict()
            return row
