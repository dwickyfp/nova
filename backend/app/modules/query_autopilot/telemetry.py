"""Best-effort bounded telemetry. Enqueuing never waits for metadata persistence."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.modules.query_autopilot.models import (
    Availability,
    Enrollment,
    Observation,
    Policy,
    Scope,
    digest,
)
from app.sql_frontend.fingerprint import QueryShape, fingerprint

logger = logging.getLogger(__name__)
PURPOSE: ContextVar[str] = ContextVar("autopilot_purpose", default="workload")


@dataclass
class ExecutionIdentity:
    id: str = field(default_factory=lambda: str(uuid4()))
    query_ids: list[str] = field(default_factory=list)
    audit_ids: list[str] = field(default_factory=list)
    capture_profile: bool = False
    profile_enabled: bool = False
    engine_roundtrip_ms: float = 0.0
    fetch_ms: float = 0.0
    scope_checked: bool = False
    policy_revision: str | None = None
    catalog: str | None = None
    database: str | None = None


EXECUTION: ContextVar[ExecutionIdentity | None] = ContextVar("nova_execution", default=None)


@contextmanager
def purpose(value: str) -> Iterator[None]:
    if value not in {"workload", "diagnostic", "experiment", "maintenance"}:
        raise ValueError("Unknown execution purpose")
    token = PURPOSE.set(value)
    try:
        yield
    finally:
        PURPOSE.reset(token)


class Collector:
    def __init__(self, capacity: int = 2048) -> None:
        self.queue: asyncio.Queue[Observation] = asyncio.Queue(capacity)
        self.enabled = True
        self.accepted = 0
        self.dropped = 0
        self.failed_batches = 0
        self.persisted = 0
        self.last_flush: float | None = None
        self.enrollments: list[Enrollment] = []
        self.samples: dict[str, tuple[str, str, int]] = {}
        self.sample_dropped = 0
        self._last_refresh = 0.0
        self.profile_sample_rate = 0.01
        self.payload_hours = 24
        self.policy_revision: str | None = None

    def request_profile(self, identity: ExecutionIdentity, sql: str, arguments: dict) -> bool:
        identity.policy_revision = self.policy_revision
        if not self.enabled or PURPOSE.get() != "workload" or not self.enrollments:
            return False
        try:
            if int(identity.id.replace("-", "")[:8], 16) / 0x100000000 >= self.profile_sample_rate:
                return False
            from app.modules.query_autopilot.payloads import eligible_sample

            shape = fingerprint(sql)
            scope = Scope(
                principal=arguments["username"],
                active_role=arguments.get("role") or None,
                security_context_version=arguments.get("security_context_version", 1),
                database=arguments.get("database") or "",
                policy_revision=identity.policy_revision,
                settings_hash=shape.settings_hash,
            )
            return any(eligible_sample(sql, scope, e) for e in self.enrollments)
        except Exception:
            return False

    def observes(self, sql: str) -> bool:
        """Whether ``observe`` would record this statement; counts an oversize drop."""
        if not self.enabled or PURPOSE.get() != "workload":
            return False
        if len(sql) > 131072:
            self.dropped += 1
            return False
        return True

    def observe(
        self,
        *,
        identity: ExecutionIdentity,
        sql: str,
        source: str,
        status: str,
        elapsed_ms: float,
        kwargs: dict,
        result=None,
        shape: QueryShape | None = None,
    ) -> None:
        if not self.observes(sql):
            return
        try:
            shape = shape or fingerprint(sql)
            scope = Scope(
                principal=kwargs["username"],
                active_role=kwargs.get("role") or None,
                security_context_version=kwargs.get("security_context_version", 1),
                catalog=(identity.catalog or "") if identity.scope_checked else "default_catalog",
                policy_revision=identity.policy_revision,
                database=(identity.database or "")
                if identity.scope_checked else (kwargs.get("database") or ""),
                settings_hash=shape.settings_hash,
            )
            item = Observation(
                id=identity.id,
                family_id=shape.family_id,
                scope=scope,
                source=source,
                status=status,
                total_ms=max(0, elapsed_ms),
                fetch_ms=getattr(result, "fetch_ms", None),
                nova_ms=getattr(result, "nova_ms", None),
                engine_roundtrip_ms=getattr(result, "engine_roundtrip_ms", None),
                engine_ms=getattr(result, "engine_ms", None),
                profile_requested=identity.profile_enabled,
                returned_rows=getattr(result, "row_count", 0),
                truncated=getattr(result, "truncated", False),
                engine_query_ids=tuple(identity.query_ids),
                audit_ids=tuple(identity.audit_ids),
                correlation=Availability.AVAILABLE
                if identity.query_ids
                else Availability.UNAVAILABLE,
                classified=shape.classified,
                canonical=shape.canonical,
                tables=shape.tables,
            )
            self.queue.put_nowait(item)
            self.accepted += 1
            if shape.replay_eligible and len(self.samples) < 32:
                from app.core.security import encrypt_password
                from app.modules.query_autopilot.payloads import eligible_sample

                for enrollment in self.enrollments:
                    if eligible_sample(sql, scope, enrollment):
                        try:
                            self.samples[item.id] = (
                                encrypt_password(sql),
                                enrollment.id,
                                enrollment.version,
                            )
                        except Exception:
                            self.sample_dropped += 1
                        break
        except Exception:
            self.dropped += 1

    async def flush(self, repo) -> None:
        batch: list[Observation] = []
        while len(batch) < 128:
            try:
                batch.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return
        try:
            from app.core.security import decrypt_password
            from app.modules.query_autopilot.payloads import PayloadStore

            payloads = PayloadStore()
            for index, item in enumerate(batch):
                sample = self.samples.pop(item.id, None)
                if not sample:
                    continue
                encrypted, enrollment_id, enrollment_version = sample
                enrollment = await repo.get("enrollments", enrollment_id)
                if not enrollment:
                    self.sample_dropped += 1
                    continue
                current = Enrollment.model_validate(enrollment)
                if (
                    not current.enabled
                    or not current.replay_opt_in
                    or current.version != enrollment_version
                    or current.scope != item.scope
                ):
                    self.sample_dropped += 1
                    continue
                evidence_id = f"sample-{item.id}"
                expires = item.observed_at + timedelta(hours=self.payload_hours)
                record: dict[str, Any] = {
                    "id": evidence_id,
                    "family_id": item.family_id,
                    "cohort_id": item.scope.cohort_id,
                    "kind": "replay_sample",
                    "availability": "unavailable",
                    "source": "opt_in_workload",
                    "collected_at": item.observed_at.isoformat(),
                    "expires_at": expires.isoformat(),
                    "summary": {
                        "profile_requested": item.profile_requested,
                        "parameter_digest": digest(decrypt_password(encrypted)),
                    },
                    "payload_ref": payloads.reference(evidence_id),
                    "query_ids": item.engine_query_ids,
                    "reason": "upload_pending",
                }
                try:
                    await repo.put(
                        "evidence",
                        evidence_id,
                        record,
                        family_id=item.family_id,
                        cohort_id=item.scope.cohort_id,
                        expires_at=expires,
                    )
                    reference = await payloads.put(evidence_id, decrypt_password(encrypted))
                    record.update(availability="available", payload_ref=reference, reason=None)
                    await repo.put(
                        "evidence",
                        evidence_id,
                        record,
                        family_id=item.family_id,
                        cohort_id=item.scope.cohort_id,
                        expires_at=expires,
                    )
                    batch[index] = item.model_copy(update={"sample_ref": evidence_id})
                except Exception:
                    self.sample_dropped += 1
            await repo.observations(batch)
            self.persisted += len(batch)
            self.last_flush = time.monotonic()
        except Exception:
            self.failed_batches += 1
            self.dropped += len(batch)
            logger.warning("Autopilot observation persistence unavailable; batch dropped")
        finally:
            for item in batch:
                self.samples.pop(item.id, None)
                self.queue.task_done()

    async def run(self, stop: asyncio.Event, repo) -> None:
        while not stop.is_set():
            if time.monotonic() - self._last_refresh >= 5:
                try:
                    policy = Policy.model_validate(await repo.get("policies", "default") or {})
                    enrollments: list[Enrollment] = []
                    after = ""
                    while True:
                        page = await repo.page("enrollments", after=after, limit=1000)
                        if not page:
                            break
                        enrollments.extend(Enrollment.model_validate(row) for row in page)
                        after = page[-1]["id"]
                    self.enabled = policy.collection_enabled
                    self.profile_sample_rate = policy.profile_sample_rate
                    self.payload_hours = policy.payload_hours
                    from app.modules.query_autopilot.runtime import current_policy_revision

                    self.policy_revision = await current_policy_revision()
                    self.enrollments = enrollments
                except Exception:
                    self.enrollments = []
                    self.policy_revision = None
                self._last_refresh = time.monotonic()
            await self.flush(repo)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.25)
        while not self.queue.empty():
            await self.flush(repo)

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "queued": self.queue.qsize(),
            "capacity": self.queue.maxsize,
            "accepted": self.accepted,
            "persisted": self.persisted,
            "dropped": self.dropped,
            "failed_batches": self.failed_batches,
            "sample_dropped": self.sample_dropped,
            "seconds_since_flush": None
            if self.last_flush is None
            else time.monotonic() - self.last_flush,
        }


collector = Collector()
