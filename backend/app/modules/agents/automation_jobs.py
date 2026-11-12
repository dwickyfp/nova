"""Persist each scheduled occurrence before running the existing Studio loop."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb

from app.common.audit import write_audit_log
from app.common.durable_jobs import EffectProof, JobDefinition, JobLease, JobRepository
from app.core.metadata_actor import acting_as
from app.core.metadata_db import MetadataConflict
from app.modules.agents.automations import (
    AUTOMATION_ACTOR,
    AutomationError,
    _worker_store,
    automation_configuration,
    delivery_journal,
    next_run,
)
from app.modules.intelligence.contracts import fingerprint

KIND = "agent.automation"


class AutomationJobs:
    async def prepare_delivery(self, job: dict, request: dict) -> None:
        from app.modules.agents.metadata_repository import _screen

        _screen(request)
        async with _worker_store() as store:
            await JobRepository(store.connection).lock_prepared(JobLease.from_record(job))
            await store.connection.execute(
                "INSERT INTO nova_meta.agent_automation_deliveries "
                "(occurrence_id,job_id,lease_generation,request_digest,request) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    job["definition_ref"],
                    job["id"],
                    job["lease_token"],
                    fingerprint(request),
                    Jsonb(request),
                ),
            )

    async def delivery_receipt(self, job: dict, receipt: dict) -> None:
        async with _worker_store() as store:
            await JobRepository(store.connection).lock_prepared(JobLease.from_record(job))
            await store.connection.execute(
                "INSERT INTO nova_meta.agent_automation_delivery_receipts "
                "(occurrence_id,response_digest,is_error) VALUES (%s,%s,%s)",
                (job["definition_ref"], receipt["response_digest"], receipt["is_error"]),
            )

    async def enqueue(self, automation: dict) -> bool:
        fire = automation["next_run_at"]
        if not isinstance(fire, datetime) or fire.tzinfo is None:
            raise ValueError("An automation occurrence requires an aware schedule instant")
        occurrence = uuid5(NAMESPACE_URL, f"nova:automation:{automation['automation_id']}:{fire}")
        async with _worker_store() as store:
            generation = await store.claim_automation(
                automation["automation_id"],
                seen_next_run_at=fire,
                seen_generation=automation["run_generation"],
                seen_revision=automation["revision"],
            )
            if generation is None:
                return False
            definition = {
                **automation_configuration(automation),
                "automation_id": automation["automation_id"],
                "revision": automation["revision"],
                "run_generation": generation,
                "next_run_at": fire.isoformat(),
            }
            digest = fingerprint(definition)
            job = await JobRepository(store.connection).enqueue(
                JobDefinition(
                    queue="automation",
                    kind=KIND,
                    definition_ref=occurrence,
                    definition_version=generation,
                    definition_digest=digest,
                    idempotency_key=occurrence,
                    resource_key=fingerprint([KIND, automation["automation_id"]]),
                    external_effect=True,
                )
            )
            await store.connection.execute(
                "INSERT INTO nova_meta.agent_automation_occurrences "
                "(id,job_id,automation_id,generation,scheduled_for,definition_digest,definition) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    occurrence,
                    job["id"],
                    automation["automation_id"],
                    generation,
                    fire,
                    digest,
                    Jsonb(definition),
                ),
            )
            return True

    async def claim(self, holder, *, seconds: int = 120) -> dict | None:
        async with _worker_store() as store:
            rows = await JobRepository(store.connection).claim(
                "automation",
                holder,
                seconds=seconds,
                kind=KIND,
            )
            return rows[0] if rows else None

    async def definition(self, job: dict) -> dict:
        async with _worker_store(read_only=True) as store:
            cursor = await store.connection.execute(
                "SELECT definition,definition_digest FROM nova_meta.agent_automation_occurrences "
                "WHERE id=%s AND job_id=%s",
                (job["definition_ref"], job["id"]),
            )
            row = await cursor.fetchone()
        if (
            row is None
            or row["definition_digest"] != job["definition_digest"]
            or fingerprint(row["definition"]) != job["definition_digest"]
        ):
            raise MetadataConflict()
        return row["definition"]

    async def prepare(self, job: dict, definition: dict, now: datetime) -> dict | None:
        async with _worker_store() as store:
            cursor = await store.connection.execute(
                "SELECT * FROM nova_meta.config_agent_automations "
                "WHERE automation_id=%s FOR UPDATE",
                (definition["automation_id"],),
            )
            current = await cursor.fetchone()
            jobs = JobRepository(store.connection)
            if (
                current is None
                or not current["enabled"]
                or current["metadata_revision"] != definition["revision"]
                or current["run_generation"] != definition["run_generation"]
            ):
                await jobs.cancel_unprepared(JobLease.from_record(job))
                if current:
                    upcoming = next_run(
                        current["schedule_kind"], current["schedule_expr"], current["timezone"], now
                    )
                    await store.connection.execute(
                        "UPDATE nova_meta.config_agent_automations SET last_status='cancelled',"
                        "next_run_at=%s WHERE automation_id=%s AND run_generation=%s "
                        "AND last_status='running'",
                        (upcoming, definition["automation_id"], definition["run_generation"]),
                    )
                return None
            return await jobs.prepare(
                JobLease.from_record(job),
                participant=KIND,
                request_digest=job["definition_digest"],
            )

    async def heartbeat(self, job: dict) -> dict:
        async with _worker_store() as store:
            return await JobRepository(store.connection).heartbeat(
                JobLease.from_record(job),
                seconds=120,
            )

    async def block(self, job: dict) -> None:
        async with _worker_store() as store:
            jobs = JobRepository(store.connection)
            if job["state"] != "VERIFYING":
                job = await jobs.verifying(JobLease.from_record(job))
            await jobs.block(JobLease.from_record(job), error="AUTOMATION_OUTCOME_UNKNOWN")
            await store.connection.execute(
                "UPDATE nova_meta.config_agent_automations SET last_status='VERIFYING' "
                "WHERE automation_id=(SELECT automation_id FROM "
                "nova_meta.agent_automation_occurrences WHERE id=%s) AND run_generation=%s "
                "AND last_status='running'",
                (job["definition_ref"], job["definition_version"]),
            )

    async def finish(self, job: dict, automation: dict, result, ran_at: datetime) -> None:
        try:
            upcoming = next_run(
                automation["schedule_kind"],
                automation["schedule_expr"],
                automation["timezone"],
                ran_at,
            )
        except AutomationError:
            upcoming = None
        proof_id = uuid4()
        async with _worker_store() as store:
            jobs = JobRepository(store.connection)
            await jobs.lock_prepared(JobLease.from_record(job))
            cursor = await store.connection.execute(
                "SELECT d.request_digest,r.response_digest,r.is_error "
                "FROM nova_meta.agent_automation_deliveries d LEFT JOIN "
                "nova_meta.agent_automation_delivery_receipts r USING(occurrence_id) "
                "WHERE d.occurrence_id=%s",
                (job["definition_ref"],),
            )
            delivery = await cursor.fetchone()
            if delivery and (delivery["response_digest"] is None or delivery["is_error"]):
                raise MetadataConflict()
            if result.status == "delivered:inbox+external" and delivery is None:
                raise MetadataConflict()
            digest = fingerprint(
                {"status": result.status, "thread_id": result.thread_id, "delivery": delivery}
            )
            verifying = await jobs.verifying(JobLease.from_record(job))
            await store.connection.execute(
                "INSERT INTO nova_meta.agent_automation_receipts "
                "(id,occurrence_id,result_digest,status,thread_id) VALUES (%s,%s,%s,%s,%s)",
                (proof_id, job["definition_ref"], digest, result.status, result.thread_id),
            )
            committed = await jobs.record_outcome(
                JobLease.from_record(verifying),
                EffectProof(proof_id, digest),
                visible=True,
            )
            # A concurrent owner edit keeps its new schedule. The receipt still
            # belongs to the immutable occurrence that actually ran.
            cursor = await store.connection.execute(
                "SELECT metadata_revision FROM nova_meta.config_agent_automations "
                "WHERE automation_id=%s FOR UPDATE",
                (automation["automation_id"],),
            )
            current = await cursor.fetchone()
            if current:
                await store.connection.execute(
                    "UPDATE nova_meta.config_agent_automations SET last_run_at=%s,last_status=%s,"
                    "last_thread_id=%s,next_run_at=CASE WHEN metadata_revision=%s THEN %s "
                    "ELSE next_run_at END WHERE automation_id=%s AND run_generation=%s "
                    "AND last_status='running'",
                    (
                        ran_at,
                        result.status,
                        result.thread_id,
                        automation["revision"],
                        upcoming,
                        automation["automation_id"],
                        automation["run_generation"],
                    ),
                )
            with acting_as(AUTOMATION_ACTOR):
                await write_audit_log(
                    event_type="AGENT_AUTOMATION",
                    user_name=AUTOMATION_ACTOR.principal,
                    action="RUN",
                    object_type="AGENT_AUTOMATION",
                    object_name=automation["automation_id"],
                    status="SUCCESS",
                    decision=result.status,
                    active_role=AUTOMATION_ACTOR.active_role,
                )
            await jobs.complete(JobLease.from_record(committed))

    async def run(self, job: dict, runner, now: datetime) -> None:
        if job["state"] != "LEASED":
            # Receipt, result and completion commit together. A reclaimed armed
            # job without them cannot establish whether an MCP mutation occurred.
            await self.block(job)
            return
        definition = await self.definition(job)
        prepared = await self.prepare(job, definition, now)
        if prepared is None:
            return
        job = prepared
        parent = asyncio.current_task()
        lease_lost = False
        lease_lock = asyncio.Lock()

        async def prepare_delivery(request):
            async with lease_lock:
                await self.prepare_delivery(job, request)

        async def record_delivery(receipt):
            async with lease_lock:
                await self.delivery_receipt(job, receipt)

        async def renew():
            nonlocal job, lease_lost
            while True:
                await asyncio.sleep(30)
                try:
                    async with lease_lock:
                        job = await self.heartbeat(job)
                except Exception:
                    lease_lost = True
                    if parent:
                        parent.cancel()
                    return

        renewal = asyncio.create_task(renew())
        try:
            with delivery_journal(prepare_delivery, record_delivery):
                result = await runner.run(definition, now=now)
        except asyncio.CancelledError:
            if lease_lost:
                raise MetadataConflict() from None
            raise
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
        if result.status.startswith("failed:") or "external_failed" in result.status:
            await self.block(job)
        else:
            await self.finish(job, definition, result, now)
