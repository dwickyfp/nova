"""Durable ML training jobs run by the standalone ML worker.

Training holds CPU and memory for up to minutes, so an API process hands it to
the ML worker and waits for the result. A job carries no credential: it names
the caller's session, and the worker trains only while that exact session is
still live with the same principal, active role and security-context version.

PostgreSQL is the authority for the work. Queueing writes the training job
(``config_ml_jobs``), its durable queue job and the outbox wakeup in the
caller's one transaction. The worker claims from the ``ml`` queue (polling;
a Redis wakeup only shortens the wait), commits a PREPARED intent before it
trains, and settles the attempt only after the model registry participant has
verified what it published. A worker that lost its lease, or crashed between
the intent and the outcome, is never replayed: the next claim verifies the
attempt's reservation instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import redis.asyncio as aioredis

from app.common.audit import write_audit_log
from app.common.durable_jobs import EffectProof, JobDefinition, JobLease, JobRepository
from app.common.metadata_actors import metadata_scope, owner_actor, service_actor
from app.core.config import settings
from app.core.metadata_db import MetadataConflict, MetadataOutcomeUnknown
from app.core.security import decrypt_password, encrypt_password
from app.modules.access_control.security_context import SecurityContext
from app.modules.migration.jobs import session_fingerprint
from app.modules.ml_engine.metadata_repository import MLMetadataRepository

logger = logging.getLogger(__name__)

ML_QUEUE = "ml"
TRAINING_KIND = "ml.training"
#: The participant whose outcome the verifier proves: the model registry.
REGISTRY_PARTICIPANT = "ml.registry"
_SESSION_KEY_PREFIX = "nova:ml:job-session:"
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
#: The training request without its SQL, which is stored encrypted.
REQUEST_FIELDS = (
    "model_name",
    "model_type",
    "algorithm",
    "target_column",
    "feature_columns",
    "hyperparameters",
    "test_size",
    "database_name",
    "timestamp_column",
    "series_column",
    "horizon",
    "frequency",
    "mode",
)


class MLJobUnavailable(RuntimeError):
    """No worker produced a result within the wait bound."""


class MLJobSessionRequired(ValueError):
    """Training on the worker needs the caller's live session."""


def definition_digest(job: dict[str, Any]) -> str:
    """SHA-256 of the immutable job definition the queue job references.

    The worker recomputes it from the stored row before training, so a changed
    definition is never executed under the original queue entry.
    """
    canonical = {
        "id": str(job["id"]),
        "actor": job["actor"],
        "active_role": job["active_role"],
        "security_context_version": int(job["security_context_version"]),
        "session_fingerprint": job["session_fingerprint"],
        "tenant": job["tenant"],
        "request_json": job["request_json"],
        "sql": hashlib.sha256(str(job["encrypted_sql"]).encode()).hexdigest(),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def job_definition(job: dict[str, Any]) -> JobDefinition:
    job_id = UUID(str(job["id"]))
    return JobDefinition(
        queue=ML_QUEUE,
        kind=TRAINING_KIND,
        definition_ref=job_id,
        definition_version=1,
        definition_digest=definition_digest(job),
        idempotency_key=uuid5(NAMESPACE_URL, f"nova:ml:training:{job_id}"),
        # One training attempt reserves its own model version.
        resource_key=_sha256(f"nova:ml:training:{job_id}"),
        external_effect=True,
    )


def job_owner(job: dict[str, Any]) -> SecurityContext:
    return owner_actor(
        job["actor"],
        job["active_role"],
        security_context_version=int(job["security_context_version"]),
    )


@dataclass
class ClaimedJob:
    """One leased queue job and the training job it references (worker-local)."""

    action: Literal["train", "verify", "complete", "skip"]
    lease: JobLease
    job: dict[str, Any]
    generation: int | None = None
    operation_id: UUID | None = None
    attempt: int = 0

    @property
    def job_id(self) -> str:
        return str(self.job["id"]) if self.job else ""


@dataclass(frozen=True)
class LocalOutcome:
    """What the worker's own, finished training attempt reported."""

    status: str
    result: dict[str, Any] | None = None
    error_kind: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class Verdict:
    kind: Literal["visible", "absent", "unresolved"]
    proof: EffectProof | None = None
    version: dict[str, Any] = field(default_factory=dict)


class MLJobStore:
    """The PostgreSQL authority for ML training jobs and their durable queue entries."""

    def __init__(self, *, verification_grace_seconds: float | None = None) -> None:
        self._grace = verification_grace_seconds

    @property
    def grace_seconds(self) -> float:
        if self._grace is not None:
            return self._grace
        # No attempt outlives the longest training budget; after it (plus the
        # time a lost lease takes to be noticed) a missing reservation is final.
        longest = max(
            settings.ML_INTERACTIVE_TIMEOUT_SECONDS,
            settings.ML_BALANCED_TIMEOUT_SECONDS,
            settings.ML_BEST_TIMEOUT_SECONDS,
        )
        return float(longest) + 300.0

    @staticmethod
    def _service() -> SecurityContext:
        return service_actor(ML_QUEUE)

    # ── Caller side (API / SQL processes, as the caller) ─────────────────

    async def submit(
        self,
        actor: SecurityContext,
        *,
        job_id: str,
        fingerprint: str,
        tenant: str,
        request: dict[str, Any],
        encrypted_sql: str,
    ) -> dict[str, Any]:
        """Persist the job, its queue entry and wakeup in one transaction."""
        async with metadata_scope(actor) as connection:
            repository = MLMetadataRepository(connection, actor)
            job, _ = await repository.create_job(
                job_id=job_id,
                fingerprint=fingerprint,
                tenant=tenant,
                request=request,
                encrypted_sql=encrypted_sql,
            )
            await JobRepository(connection).enqueue(
                job_definition({**job, "encrypted_sql": encrypted_sql})
            )
            return job

    async def get(self, actor: SecurityContext, job_id: str) -> dict[str, Any] | None:
        async with metadata_scope(actor, read_only=True) as connection:
            return await MLMetadataRepository(connection, actor).get_job(job_id)

    async def request_cancel(self, actor: SecurityContext, job_id: str) -> None:
        async with metadata_scope(actor) as connection:
            await MLMetadataRepository(connection, actor).request_cancel(job_id)

    async def withdraw(
        self, actor: SecurityContext, job_id: str, *, error_kind: str, error_message: str
    ) -> bool:
        """Fail a job no worker claimed; its queue entry is then cancelled at claim."""
        async with metadata_scope(actor) as connection:
            return await MLMetadataRepository(connection, actor).withdraw_queued_job(
                job_id, error_kind=error_kind, error_message=error_message
            )

    # ── Worker side (ML worker login, SERVICE actor) ─────────────────────

    async def claim_next(self, holder: UUID, *, lease_seconds: int) -> ClaimedJob | None:
        """Lease one due queue job. A training claim commits its PREPARED intent."""
        actor = self._service()
        async with metadata_scope(actor) as connection:
            jobs = JobRepository(connection)
            repository = MLMetadataRepository(connection, actor)
            rows = await jobs.claim(ML_QUEUE, holder, seconds=lease_seconds, limit=1)
            if not rows:
                return None
            row = rows[0]
            lease = JobLease.from_record(row)
            job = await repository.get_job_for_worker(str(row["definition_ref"]))
            if (
                row["kind"] != TRAINING_KIND
                or job is None
                or definition_digest(job) != row["definition_digest"]
            ):
                # Never execute a definition that is missing or changed.
                logger.error("ML queue job %s has no matching definition", row["id"])
                if row["state"] == "LEASED":
                    await jobs.cancel_unprepared(lease)
                elif row["state"] == "VERIFYING":
                    await jobs.block(lease, error="ML_DEFINITION_MISMATCH")
                return ClaimedJob("skip", lease, job or {})
            if row["state"] == "LEASED":
                generation = await repository.claim_job(str(job["id"]))
                if generation is None:
                    # Withdrawn, cancelled or claimed elsewhere: nothing to train.
                    await jobs.cancel_unprepared(lease)
                    return ClaimedJob("skip", lease, job)
                prepared = await jobs.prepare(
                    lease,
                    participant=REGISTRY_PARTICIPANT,
                    request_digest=row["definition_digest"],
                )
                intent = await jobs.intent(row["id"], prepared["attempt"])
                assert intent is not None
                return ClaimedJob(
                    "train",
                    JobLease.from_record(prepared),
                    job,
                    generation=generation,
                    operation_id=intent["operation_id"],
                    attempt=int(prepared["attempt"]),
                )
            generation = int(job["lease_generation"]) if job["status"] == "running" else None
            if row["state"] == "COMMITTED":
                return ClaimedJob(
                    "complete", lease, job, generation=generation, attempt=int(row["attempt"])
                )
            intent = await jobs.intent(row["id"], row["attempt"])
            if intent is None:
                await jobs.block(lease, error="ML_INTENT_MISSING")
                return ClaimedJob("skip", lease, job)
            return ClaimedJob(
                "verify",
                lease,
                job,
                generation=generation,
                operation_id=intent["operation_id"],
                attempt=int(row["attempt"]),
            )

    async def heartbeat(self, claimed: ClaimedJob, *, lease_seconds: int) -> tuple[bool, bool]:
        """Extend the lease. Returns (still held, cancellation requested)."""
        actor = self._service()
        cancel = False
        try:
            async with metadata_scope(actor) as connection:
                row = await JobRepository(connection).heartbeat(
                    claimed.lease, seconds=lease_seconds
                )
                if claimed.generation is not None:
                    held, cancel = await MLMetadataRepository(connection, actor).heartbeat_job(
                        claimed.job_id, generation=claimed.generation
                    )
                    if not held:
                        raise MetadataConflict()
        except (MetadataConflict, MetadataOutcomeUnknown):
            # Lost, or unknown whether the revision advanced: stop acting on it.
            return False, False
        claimed.lease = JobLease.from_record(row)
        return True, cancel

    async def _intent_age(self, claimed: ClaimedJob) -> float:
        actor = self._service()
        async with metadata_scope(actor, read_only=True) as connection:
            intent = await JobRepository(connection).intent(claimed.lease.job_id, claimed.attempt)
            cursor = await connection.execute("SELECT clock_timestamp() AS now")
            now = await cursor.fetchone()
        if intent is None or now is None:
            return 0.0
        created: datetime = intent["created_at"]
        return (now["now"] - created).total_seconds()

    async def verify(self, claimed: ClaimedJob, *, final: bool) -> Verdict:
        """The model registry participant's proof of what this attempt published.

        ``final`` means this worker's own attempt has finished, so nothing of
        it is still in flight. A reservation left TRAINING is abandoned first,
        which fences a stale attempt out of publishing it.
        """
        from app.modules.ml_engine.registry.repository import model_registry_repository

        assert claimed.operation_id is not None
        token = str(claimed.operation_id)
        reader = self._service()
        version = await model_registry_repository.version_by_reservation(token, actor=reader)
        if version is not None and version["status"] == "TRAINING":
            await model_registry_repository.abort_version(
                version["model_id"], int(version["version"]), actor=job_owner(claimed.job)
            )
            version = await model_registry_repository.version_by_reservation(token, actor=reader)
        if version is not None and version["status"] == "READY":
            return Verdict(
                "visible",
                EffectProof(
                    uuid5(
                        NAMESPACE_URL, f"nova:ml:version:{version['model_id']}:{version['version']}"
                    ),
                    str(version["artifact_sha256"]),
                ),
                dict(version),
            )
        if version is not None and version["status"] == "FAILED":
            return Verdict("absent", self._absent(token, "failed"), dict(version))
        if version is None and (final or await self._intent_age(claimed) > self.grace_seconds):
            return Verdict("absent", self._absent(token, "none"))
        return Verdict("unresolved")

    @staticmethod
    def _absent(token: str, reason: str) -> EffectProof:
        return EffectProof(
            uuid5(NAMESPACE_URL, f"nova:ml:reservation:{token}"),
            _sha256(f"absent:{token}:{reason}"),
        )

    async def settle(self, claimed: ClaimedJob, outcome: LocalOutcome | None) -> str:
        """Record the verified outcome, then finish both records in one transaction.

        ``outcome`` is ``None`` for a recovered attempt. Returns the training
        job status written, ``"blocked"`` or ``"lost"`` (the lease moved on).
        """
        verdict = None
        if claimed.action != "complete":
            verdict = await self.verify(claimed, final=outcome is not None)
        actor = self._service()
        try:
            async with metadata_scope(actor) as connection:
                jobs = JobRepository(connection)
                lease = claimed.lease
                status, fields = self._domain_outcome(verdict, outcome)
                if verdict is not None:
                    if claimed.action == "train":
                        lease = JobLease.from_record(await jobs.verifying(lease))
                    if verdict.kind == "unresolved":
                        await jobs.block(lease, error="ML_OUTCOME_UNRESOLVED")
                        status = "blocked"
                    else:
                        assert verdict.proof is not None
                        visible = verdict.kind == "visible"
                        lease = JobLease.from_record(
                            await jobs.record_outcome(
                                lease,
                                verdict.proof,
                                visible=visible,
                                error=None if visible else self._error_class(fields),
                            )
                        )
                        if visible:
                            await jobs.complete(lease)
                else:
                    await jobs.complete(lease)
                if claimed.generation is not None:
                    await MLMetadataRepository(connection, actor).finish_job(
                        claimed.job_id,
                        "interrupted" if status == "blocked" else status,
                        generation=claimed.generation,
                        **fields,
                    )
        except (MetadataConflict, MetadataOutcomeUnknown):
            logger.warning("ML job %s lease moved on before it settled", claimed.job_id)
            return "lost"
        return status

    @staticmethod
    def _error_class(fields: dict[str, Any]) -> str:
        kind = str(fields.get("error_kind") or "failed")
        return "ML_" + "".join(c if c.isalnum() else "_" for c in kind.upper())[:60]

    @staticmethod
    def _domain_outcome(
        verdict: Verdict | None, outcome: LocalOutcome | None
    ) -> tuple[str, dict[str, Any]]:
        if verdict is None or verdict.kind == "visible":
            if outcome is not None and outcome.status == "succeeded":
                return "succeeded", {"result": outcome.result}
            version = verdict.version if verdict is not None else {}
            # The version is published even if the local run did not report it.
            return "succeeded", {
                "result": {
                    "model_id": version.get("model_id"),
                    "version": version.get("version"),
                    "status": "READY",
                }
            }
        if verdict.kind == "unresolved" or outcome is None:
            return "interrupted", {
                "error_kind": "worker_interrupted",
                "error_message": "Training was interrupted",
            }
        if outcome.status == "succeeded":
            return "failed", {
                "error_kind": "failed",
                "error_message": "Training finished without a published model version",
            }
        return outcome.status, {
            "error_kind": outcome.error_kind,
            "error_message": outcome.error_message,
        }

    async def interrupt_stale(self, *, older_than_seconds: int = 120) -> list[str]:
        actor = self._service()
        async with metadata_scope(actor) as connection:
            return await MLMetadataRepository(connection, actor).interrupt_stale_jobs(
                older_than_seconds=older_than_seconds
            )

    async def prune_finished(self) -> int:
        actor = self._service()
        async with metadata_scope(actor) as connection:
            return await MLMetadataRepository(connection, actor).prune_finished_jobs()

    async def ensure_schema(self) -> None:
        """No-op: the PostgreSQL migrator owns this schema.

        Kept only because process startup still calls it; the startup owner
        removes that call at cutover.
        """


ml_job_store = MLJobStore()
#: Former name, still imported by process startup.
ml_job_repo = ml_job_store


def training_sql(job: dict[str, Any]) -> str:
    return decrypt_password(job["encrypted_sql"])


def caller_actor(user: dict[str, Any]) -> SecurityContext:
    """The caller's metadata actor; fails closed without exactly one active role."""
    return owner_actor(
        user.get("username"),
        user.get("active_role"),
        session_id=user.get("session_id"),
        security_context_version=int(user.get("security_context_version") or 1),
    )


async def submit_training(
    *, session_id: str | None, user: dict[str, Any], training_sql: str, request: dict[str, Any]
) -> str:
    """Queue one training request for the worker. Returns the job id.

    ``user`` supplies the principal, active role, security-context version and
    tenant the worker must find unchanged on the session before it reads data.
    """
    if not session_id:
        raise MLJobSessionRequired("Training on the worker requires a live session")
    unknown = set(request) - set(REQUEST_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported training options: {sorted(unknown)}")
    actor = caller_actor({**user, "session_id": session_id})
    job_id = str(uuid4())
    key = f"{_SESSION_KEY_PREFIX}{job_id}"
    await write_audit_log(
        event_type="ml",
        user_name=user["username"],
        action="queue_training",
        object_type="ml_job",
        object_name=job_id,
        status="requested",
        session_id=session_id,
    )
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        # The session link is session state, not work authority: without it the
        # worker refuses the job rather than train under another identity.
        await client.set(key, session_id, ex=settings.SESSION_TTL_SECONDS)
        try:
            await ml_job_store.submit(
                actor,
                job_id=job_id,
                fingerprint=session_fingerprint(session_id),
                tenant=user.get("tenant") or "default",
                request=request,
                # The SQL can name storage or carry literals the caller would
                # not want readable in a metadata table.
                encrypted_sql=encrypt_password(training_sql),
            )
        except Exception:
            await client.delete(key)
            raise
    finally:
        await client.aclose()
    return job_id


async def wait_for_training(
    job_id: str, *, actor: SecurityContext, timeout_seconds: float
) -> dict[str, Any]:
    """Wait for the worker's result, raising what a local run would have raised."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while True:
            job = await ml_job_store.get(actor, job_id)
            if job is None:
                raise MLJobUnavailable("ML training job was not found")
            status = job["status"]
            if status == "succeeded":
                return json.loads(job["result_json"] or "{}")
            if status in TERMINAL:
                raise _failure(job)
            waited = loop.time() - started
            if status == "queued" and waited >= settings.ML_JOB_QUEUE_TIMEOUT_SECONDS:
                # Nothing claimed it: withdraw the job so a worker that appears
                # later does not train a model nobody is waiting for.
                if await ml_job_store.withdraw(
                    actor,
                    job_id,
                    error_kind="worker_unavailable",
                    error_message="No ML worker picked up the training job",
                ):
                    raise MLJobUnavailable(
                        "No ML worker picked up the training job; is nova-worker running?"
                    )
                continue
            if waited >= timeout_seconds:
                await ml_job_store.request_cancel(actor, job_id)
                raise MLJobUnavailable(f"ML training timed out waiting for the worker ({job_id})")
            await asyncio.sleep(settings.ML_JOB_POLL_SECONDS)
    except asyncio.CancelledError:
        # The caller went away; stop the training it started.
        await asyncio.shield(ml_job_store.request_cancel(actor, job_id))
        raise


def _failure(job: dict[str, Any]) -> Exception:
    message = job.get("error_message") or f"ML training {job['status']}"
    if job.get("error_kind") == "invalid_request":
        return ValueError(message)
    if job.get("error_kind") == "session_expired":
        return PermissionError(message)
    return RuntimeError(message)


def public_status(job: dict[str, Any]) -> dict[str, Any]:
    request = json.loads(job["request_json"] or "{}")
    return {
        "job_id": job["id"],
        "status": job["status"],
        "model_name": request.get("model_name"),
        "mode": request.get("mode"),
        "error": job.get("error_message"),
        "result": json.loads(job["result_json"]) if job.get("result_json") else None,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


async def job_session_id(client: aioredis.Redis, job_id: str) -> str | None:
    value = await client.get(f"{_SESSION_KEY_PREFIX}{job_id}")
    return value.decode() if isinstance(value, bytes) else value


async def clear_job_session(client: aioredis.Redis, job_id: str) -> None:
    await client.delete(f"{_SESSION_KEY_PREFIX}{job_id}")
