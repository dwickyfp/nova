"""Maintenance participant that installs ``AI_*`` engine functions from definitions.

An alias change commits an immutable function definition and an
``ai.function.reconcile`` job on the maintenance queue (see
``registrations.py``). The maintenance worker's job dispatcher claims that job
and calls :meth:`AIFunctionRegistrationParticipant.handle` with the claimed row.

The participant follows the durable-job effect contract:

1. It refuses a stale or unsupported definition before any engine call
   (``CANCELLED`` while the job is still unprepared).
2. It commits ``PREPARED`` with the request digest, then ``SUBMITTED``, and only
   then runs ``DROP``/``CREATE`` in StarRocks.
3. It reads the engine's stored function back. A matching body is a
   ``VISIBLE`` receipt (job ``COMMITTED`` then ``COMPLETED``). A statement the
   engine definitely refused, with the function not matching, is an ``ABSENT``
   receipt (job ``FAILED``, not retryable). Anything else is ``BLOCKED`` for
   operator reconciliation.

A job recovered after lease loss arrives in ``VERIFYING`` with the same attempt.
The participant only observes the engine: it never repeats ``DROP``/``CREATE``
for an armed intent. Operator recovery of a ``BLOCKED`` job goes through the
shared durable-job reconciliation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any, Protocol
from uuid import uuid4

import asyncmy.errors

from app.common.durable_jobs import EffectProof, JobLease, JobRepository
from app.common.metadata_actors import Connection, metadata_scope, service_actor
from app.core.metadata_db import MetadataConflict
from app.modules.llm_functions.registrations import FunctionRegistrationRepository
from app.modules.llm_functions.service import (
    RenderedFunction,
    function_body_matches,
    llm_function_service,
    render_function,
)

logger = logging.getLogger(__name__)

#: The durable job kind this participant owns on the maintenance queue.
JOB_KIND = "ai.function.reconcile"
#: The intent's participant identity.
PARTICIPANT = "starrocks.ai_function"

Transaction = Callable[[], AbstractAsyncContextManager[Connection]]


class EngineRefusal(Exception):
    """The engine answered the statement with an error: it is not in flight."""


class FunctionEngine(Protocol):
    async def apply(self, rendered: RenderedFunction) -> None: ...

    async def stored_body(self, function_name: str) -> str | None: ...


class StarRocksFunctionEngine:
    """Applies and observes global ``AI_*`` functions in StarRocks."""

    async def apply(self, rendered: RenderedFunction) -> None:
        try:
            connection = await llm_function_service._connect()
        except asyncmy.errors.Error as exc:
            # No connection means no statement was sent.
            raise EngineRefusal() from exc
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(rendered.drop_sql)
                await cursor.execute(rendered.create_sql)
        except asyncmy.errors.Error as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            # Server error codes (< 2000) are answers; client codes mean the
            # connection was lost and the statement's outcome is unknown.
            if code is not None and code < 2000:
                raise EngineRefusal() from exc
            raise
        finally:
            connection.close()

    async def stored_body(self, function_name: str) -> str | None:
        connection = await llm_function_service._connect()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SHOW FULL GLOBAL FUNCTIONS")
                for row in await cursor.fetchall():
                    values = list(row.values()) if isinstance(row, dict) else list(row)
                    if str(values[0]).split("(", 1)[0].strip().upper() == function_name:
                        return str(values[-1])
        finally:
            connection.close()
        return None


def _maintenance_transaction() -> AbstractAsyncContextManager[Connection]:
    return metadata_scope(service_actor("maintenance"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AIFunctionRegistrationParticipant:
    """Handles one claimed ``ai.function.reconcile`` job. Returns its final state."""

    def __init__(
        self,
        *,
        engine: FunctionEngine | None = None,
        transaction: Transaction = _maintenance_transaction,
    ) -> None:
        self._engine = engine or StarRocksFunctionEngine()
        self._transaction = transaction

    async def handle(self, job: dict[str, Any]) -> str:
        if job.get("kind") != JOB_KIND or job.get("queue") != "maintenance":
            raise ValueError("Not an AI function registration job")
        try:
            return await self._handle(job)
        except MetadataConflict:
            # The lease moved on (expired and reclaimed, or a newer revision).
            # The new holder verifies; this worker must not act again.
            logger.warning("AI function registration lease lost")
            return "LEASE_LOST"

    async def _handle(self, job: dict[str, Any]) -> str:
        lease = JobLease.from_record(job)
        if job["state"] == "COMMITTED":
            return await self._complete(lease)
        async with self._transaction() as connection:
            registrations = FunctionRegistrationRepository(connection)
            definition = await registrations.definition(job["definition_ref"])
            head = await registrations.head(definition["function_type"]) if definition else None
        valid = (
            definition is not None
            and definition["version"] == job["definition_version"]
            and definition["digest"] == job["definition_digest"]
            and _digest(definition["payload"]) == definition["digest"]
        )
        rendered = (
            render_function(json.loads(definition["payload"])) if valid and definition else None
        )

        refused = False
        if job["state"] == "LEASED":
            superseded = head is None or head["version"] != job["definition_version"]
            if rendered is None or superseded:
                async with self._transaction() as connection:
                    row = await JobRepository(connection).cancel_unprepared(lease)
                return str(row["state"])
            async with self._transaction() as connection:
                row = await JobRepository(connection).prepare(
                    lease, participant=PARTICIPANT, request_digest=_digest(rendered.create_sql)
                )
            lease = JobLease.from_record(row)
            async with self._transaction() as connection:
                row = await JobRepository(connection).submitted(lease)
            lease = JobLease.from_record(row)
            try:
                await self._engine.apply(rendered)
            except EngineRefusal:
                refused = True
                logger.warning("Engine refused the AI function %s", rendered.function_name)
            except Exception as exc:  # noqa: BLE001 - outcome is verified below
                logger.warning("AI function apply outcome unknown: %s", type(exc).__name__)
            async with self._transaction() as connection:
                row = await JobRepository(connection).verifying(lease)
            lease = JobLease.from_record(row)
        elif job["state"] != "VERIFYING":
            raise ValueError("Unexpected AI function registration job state")
        return await self._verify(lease, rendered, refused=refused)

    async def _verify(
        self, lease: JobLease, rendered: RenderedFunction | None, *, refused: bool
    ) -> str:
        body: str | None = None
        observed = rendered is not None
        if rendered is not None:
            try:
                body = await self._engine.stored_body(rendered.function_name)
            except Exception as exc:  # noqa: BLE001 - no observation, no proof
                logger.warning("AI function could not be observed: %s", type(exc).__name__)
                observed = False
        visible = (
            rendered is not None and body is not None and function_body_matches(rendered, body)
        )
        proof = EffectProof(uuid4(), _digest(body if body is not None else "absent"))
        async with self._transaction() as connection:
            jobs = JobRepository(connection)
            if visible:
                row = await jobs.record_outcome(lease, proof, visible=True)
            elif refused and observed:
                # The engine answered with an error and the definition is not
                # installed: nothing remains in flight for this attempt.
                row = await jobs.record_outcome(lease, proof, visible=False, error="ENGINE_REFUSED")
            else:
                row = await jobs.block(lease, error="OUTCOME_UNKNOWN")
        if row["state"] == "COMMITTED":
            return await self._complete(JobLease.from_record(row))
        return str(row["state"])

    async def _complete(self, lease: JobLease) -> str:
        async with self._transaction() as connection:
            row = await JobRepository(connection).complete(lease)
        return str(row["state"])


class AIFunctionRegistrationWorker:
    """Poll durable registration intents, one bounded engine operation at a time.

    The lease exceeds the operation deadline. Cancellation leaves the durable
    phase intact so a replacement can verify an armed effect without replaying it.
    Redis wakeups are unnecessary for correctness or for starting pending work.
    """

    LEASE_SECONDS = 300
    OPERATION_SECONDS = 240
    POLL_SECONDS = 1

    def __init__(
        self,
        *,
        participant: AIFunctionRegistrationParticipant | None = None,
        transaction: Transaction = _maintenance_transaction,
    ) -> None:
        self._transaction = transaction
        self._participant = participant or AIFunctionRegistrationParticipant(
            transaction=transaction
        )
        self._holder = uuid4()

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            async with self._transaction() as connection:
                jobs = await JobRepository(connection).claim(
                    "maintenance",
                    self._holder,
                    seconds=self.LEASE_SECONDS,
                    limit=1,
                    kind=JOB_KIND,
                )
            if jobs:
                try:
                    await asyncio.wait_for(
                        self._participant.handle(jobs[0]),
                        self.OPERATION_SECONDS,
                    )
                except TimeoutError:
                    logger.warning("AI function registration timed out; recovery must verify it")
            else:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.POLL_SECONDS)
