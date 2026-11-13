"""Autopilot metadata I/O over the PostgreSQL control store.

Callers keep the collection-oriented interface (``get``/``put``/``page``) the
service and worker were written against; each write is routed to the store
operation that enforces its rule:

* policies and enrollments change only from the version this scope read;
* a candidate changes only from the metadata revision this scope read, an
  approval goes through the bound ``approve`` path and a cleared approval is
  withdrawn, never carried;
* jobs are created once, finished only by the lease holder (or, for an
  in-request decision, from the version read), and checkpoints stay cursors;
* experiments, actions and outcomes are fenced by the claimed job lease, and
  an action records its intent before it is acknowledged.

Every operation runs as an explicit actor: the request user, or one of the
service principals below. Without one the repository refuses rather than
reaching metadata anonymously. Each call is its own transaction unless the
caller opened a unit with :meth:`AutopilotRepository.transaction`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.metadata_scope import acting_as_sync, metadata_transaction
from app.modules.access_control.security_context import SecurityContext
from app.modules.query_autopilot.metadata_repository import AutopilotMetadataRepository
from app.modules.query_autopilot.models import Observation

# Service principals. The process login (workload) is chosen at startup; these
# name who acts inside it.
WORKER_ACTOR = SecurityContext(
    "query-autopilot-worker", "QUERY_AUTOPILOT", principal_type="SERVICE"
)
SCHEDULER_ACTOR = SecurityContext(
    "query-autopilot-scheduler", "QUERY_AUTOPILOT", principal_type="SERVICE"
)
TELEMETRY_ACTOR = SecurityContext(
    "query-autopilot-telemetry", "QUERY_AUTOPILOT", principal_type="SERVICE"
)

_CAS_KINDS = ("opportunities", "policies", "enrollments")
_ABSENT: dict[str, Any] = {}


class ActorRequired(ValueError):
    def __init__(self) -> None:
        super().__init__("autopilot_metadata_actor_required")


@dataclass
class _Scope:
    actor: SecurityContext
    lease: tuple[str, int] | None = None
    task: asyncio.Task[Any] | None = None
    rows: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


_scope: ContextVar[_Scope | None] = ContextVar("query_autopilot_metadata_scope", default=None)


def request_actor(user: dict) -> SecurityContext:
    security = user.get("security")
    return security if isinstance(security, SecurityContext) else SecurityContext.from_session(user)


@contextlib.contextmanager
def acting(actor: SecurityContext, *, lease: tuple[str, int] | None = None) -> Iterator[None]:
    """Run the enclosed metadata calls as ``actor``, optionally holding a job lease.

    A new scope also starts a fresh record of the revisions read, so a write
    can only be based on what this scope itself observed.
    """
    if not isinstance(actor, SecurityContext):
        raise ActorRequired()
    token = _scope.set(_Scope(actor, lease, _task()))
    try:
        with acting_as_sync(actor):
            yield
    finally:
        _scope.reset(token)


def current_lease() -> tuple[str, int] | None:
    scope = _scope.get()
    return scope.lease if scope else None


def _current() -> _Scope:
    scope = _scope.get()
    if scope is None:
        raise ActorRequired()
    if scope.task is not _task():
        scope = _Scope(scope.actor, scope.lease, _task())
        _scope.set(scope)
    return scope


def _task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _when(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class AutopilotRepository:
    @contextlib.asynccontextmanager
    async def _store(
        self, *, read_only: bool = False
    ) -> AsyncIterator[AutopilotMetadataRepository]:
        scope = _current()
        async with metadata_transaction(scope.actor, read_only=read_only) as unit:
            yield AutopilotMetadataRepository(unit.connection, unit.actor)

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Keep the enclosed calls in one metadata transaction (a service operation)."""
        scope = _current()
        async with metadata_transaction(scope.actor):
            yield

    def _seen(self, kind: str, identifier: str, row: dict[str, Any] | None) -> None:
        if kind in _CAS_KINDS:
            _current().rows[kind, identifier] = row if row is not None else _ABSENT

    def _read(self, kind: str, identifier: str) -> dict[str, Any]:
        row = _current().rows.get((kind, identifier))
        if row is None:
            # Writes are based on what this scope read; never on a blind upsert.
            raise ValueError("autopilot_record_not_read_before_change")
        return row

    def _lease(self) -> tuple[str, int]:
        lease = current_lease()
        if lease is None:
            raise ValueError("autopilot_job_lease_required")
        return lease

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get(self, kind: str, identifier: str) -> dict | None:
        async with self._store(read_only=True) as store:
            row = await store.get(kind, identifier)
        self._seen(kind, identifier, row)
        return dict(row["payload"]) if row else None

    async def page(
        self,
        kind: str,
        *,
        after: str = "",
        limit: int = 100,
        family_id: str | None = None,
        cohort_id: str | None = None,
        state: str | None = None,
        since: datetime | None = None,
        created_since: datetime | None = None,
        before: datetime | None = None,
    ) -> list[dict]:
        async with self._store(read_only=True) as store:
            rows = await store.page(
                kind,
                after=after,
                limit=limit,
                family_id=family_id,
                cohort_id=cohort_id,
                state=state,
                since=since,
                created_since=created_since,
                before=before,
            )
        return [dict(row["payload"]) for row in rows]

    async def count(self, kind: str) -> int:
        async with self._store(read_only=True) as store:
            return await store.count(kind)

    # ── Writes ────────────────────────────────────────────────────────────

    async def put(
        self,
        kind: str,
        identifier: str,
        payload: dict,
        *,
        family_id: str = "",
        cohort_id: str = "",
        version: int = 1,
        state: str = "",
        expires_at: datetime | None = None,
        created_at: datetime | None = None,
        expected_version: int | None = None,
        insert_only: bool = False,
    ) -> bool:
        """Write one record through the rule that governs its collection.

        Returns False only where the source did: an insert-only record that
        already exists, or a job finish whose lease or version was lost.
        """
        if kind in ("policies", "enrollments"):
            return await self._definition(kind, identifier, payload, cohort_id)
        if kind == "opportunities":
            await self._candidate(identifier, payload, family_id, cohort_id)
            return True
        if kind == "jobs":
            return await self._job(
                identifier,
                payload,
                family_id=family_id,
                cohort_id=cohort_id,
                expected_version=expected_version,
                insert_only=insert_only,
            )
        if kind == "experiments":
            job_id, generation = self._lease()
            async with self._store() as store:
                await store.save_experiment(
                    identifier,
                    payload,
                    job_id=job_id,
                    generation=generation,
                    family_id=family_id,
                    cohort_id=cohort_id,
                    state=str(state),
                )
            return True
        if kind == "actions":
            return await self._action(identifier, payload, family_id, cohort_id)
        if kind == "outcomes":
            await self._outcome(identifier, payload, family_id, cohort_id, str(state))
            return True
        if kind == "evidence":
            async with self._store() as store:
                await store.put_evidence(
                    identifier,
                    payload,
                    family_id=family_id,
                    cohort_id=cohort_id,
                    expires_at=_when(expires_at),
                )
            return True
        if kind == "incidents":
            async with self._store() as store:
                _, created = await store.record_incident_once(
                    identifier,
                    payload,
                    family_id=family_id,
                    cohort_id=cohort_id,
                )
            return created
        if kind in ("families", "rollups", "baselines"):
            async with self._store() as store:
                await store.put_summary(
                    kind,
                    identifier,
                    payload,
                    family_id=family_id,
                    cohort_id=cohort_id,
                    state=str(state),
                    # Only a baseline keeps its window start; rollups age by write time.
                    created_at=created_at if kind == "baselines" else None,
                )
            return True
        raise ValueError("Unsupported autopilot collection")

    async def _definition(
        self,
        kind: str,
        identifier: str,
        payload: dict,
        cohort_id: str,
    ) -> bool:
        read = self._read(kind, identifier)
        async with self._store() as store:
            row = await store.save_definition(
                kind,
                identifier,
                payload,
                cohort_id=cohort_id,
                expected_version=int(read["version"]) if read else None,
            )
        self._seen(kind, identifier, row)
        return True

    async def _candidate(
        self,
        identifier: str,
        payload: dict,
        family_id: str,
        cohort_id: str,
    ) -> None:
        read = self._read("opportunities", identifier)
        async with self._store() as store:
            if not read:
                row = await store.propose(
                    identifier,
                    payload,
                    family_id=family_id,
                    cohort_id=cohort_id,
                )
            elif payload.get("state") == "APPROVED" and read["state"] != "APPROVED":
                row = await store.approve(
                    identifier,
                    payload,
                    expected_revision=int(read["metadata_revision"]),
                    approval_digest=str(payload.get("approval_digest") or ""),
                    expires_at=_when(payload.get("approval_expires_at")),  # type: ignore[arg-type]
                )
            else:
                row = await store.update_candidate(
                    identifier,
                    payload,
                    expected_revision=int(read["metadata_revision"]),
                    withdraw_approval=bool(
                        read.get("approval_digest") and not payload.get("approval_digest")
                    ),
                )
        self._seen("opportunities", identifier, row)

    async def _job(
        self,
        identifier: str,
        payload: dict,
        *,
        family_id: str,
        cohort_id: str,
        expected_version: int | None,
        insert_only: bool,
    ) -> bool:
        async with self._store() as store:
            if payload.get("state") == "CHECKPOINT":
                await store.save_checkpoint(identifier, payload)
                return True
            if insert_only:
                _, created = await store.create_job_once(
                    identifier,
                    payload,
                    family_id=family_id,
                    cohort_id=cohort_id,
                )
                return created
            if expected_version is None:
                raise ValueError("A job changes only from the version read")
            lease = current_lease()
            if lease is not None and lease[0] == identifier:
                return await store.finish_job(identifier, payload, generation=lease[1]) is not None
            await store.complete_decision(identifier, payload, expected_version=expected_version)
            return True

    async def _action(
        self,
        identifier: str,
        payload: dict,
        family_id: str,
        cohort_id: str,
    ) -> bool:
        job_id, generation = self._lease()
        async with self._store() as store:
            if payload.get("state") == "APPLYING":
                _, created = await store.record_action_intent(
                    identifier,
                    payload,
                    job_id=job_id,
                    generation=generation,
                    family_id=family_id,
                    cohort_id=cohort_id,
                )
                return created
            await store.acknowledge_action(
                identifier,
                payload,
                job_id=job_id,
                generation=generation,
            )
            return True

    async def _outcome(
        self,
        identifier: str,
        payload: dict,
        family_id: str,
        cohort_id: str,
        state: str,
    ) -> None:
        job_id, generation = self._lease()
        async with self._store() as store:
            existing = await store.get("outcomes", identifier)
            stored = existing["payload"] if existing else None
            if (
                stored is not None
                and stored.get("rollback") == "pending"
                and payload.get("rollback") not in (None, "pending")
            ):
                await store.resolve_rollback(
                    identifier,
                    str(payload["rollback"]),
                    job_id=job_id,
                    generation=generation,
                )
                return
            await store.record_outcome(
                identifier,
                payload,
                job_id=job_id,
                generation=generation,
                family_id=family_id,
                cohort_id=cohort_id,
                state=state,
            )

    async def observations(self, observations: list[Observation]) -> None:
        if not observations:
            return
        async with self._store() as store:
            await store.record_observations(
                [
                    {
                        "id": item.id,
                        "family_id": item.family_id,
                        "cohort_id": item.scope.cohort_id,
                        "state": item.status,
                        "payload": item.model_dump(mode="json"),
                        "observed_at": item.observed_at,
                        "expires_at": item.observed_at + timedelta(days=7),
                    }
                    for item in observations
                ]
            )

    async def delete(self, kind: str, identifier: str) -> None:
        """Delete expired evidence; durable decisions and jobs are never deleted by id."""
        if kind != "evidence":
            raise ValueError("Only expired evidence is deleted by id")
        async with self._store() as store:
            await store.delete_expired_evidence(identifier)

    async def cleanup_before(self, kind: str, before: datetime) -> None:
        if kind not in {"observations", "rollups", "baselines"}:
            raise ValueError("Durable decisions require explicit administrative cleanup")
        async with self._store() as store:
            await store.cleanup_before(kind, before)

    async def purge_finished_jobs(self, before: datetime) -> int:
        async with self._store() as store:
            return await store.purge_finished_jobs(before)

    # ── Worker leases ─────────────────────────────────────────────────────

    async def claim_job(self, identifier: str, *, expected_version: int) -> int | None:
        async with self._store() as store:
            return await store.claim_job(identifier, expected_version=expected_version)

    async def retake_job(self, identifier: str, *, older_than_seconds: float) -> int | None:
        async with self._store() as store:
            return await store.retake_job(identifier, older_than_seconds=older_than_seconds)

    async def heartbeat_job(self, identifier: str, *, generation: int) -> bool:
        async with self._store() as store:
            return await store.heartbeat_job(identifier, generation=generation)


repository = AutopilotRepository()
