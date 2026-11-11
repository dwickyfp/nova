"""Durable queue and outbox faults on the existing disposable PostgreSQL 17."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
import pytest_asyncio

from app.common.durable_jobs import (
    EffectProof,
    JobDefinition,
    JobLease,
    JobRepository,
    OutboxRepository,
)
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
    MetadataReadOnlyMaintenance,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg


def definition(**changes):
    return replace(
        JobDefinition(
            queue="task",
            kind="sql.execute",
            definition_ref=uuid4(),
            definition_version=1,
            definition_digest="a" * 64,
            idempotency_key=uuid4(),
            resource_key=uuid4().hex * 2,
            external_effect=True,
        ),
        **changes,
    )


def user(name="alice", role="ANALYST", version=1):
    return SecurityContext(principal=name, active_role=role, security_context_version=version)


@pytest_asyncio.fixture
async def jobs(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="c" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false",
        (epoch,),
    )
    databases = {}

    @asynccontextmanager
    async def transaction(role="web", identity=None):
        if role not in databases:
            db = MetadataDatabase(environment.role_config("nova_meta_" + role))
            await db.open(schema_version=len(load_migrations()), metadata_epoch=epoch)
            databases[role] = db
        actor = identity or (
            user()
            if role == "web"
            else SecurityContext(
                principal="runtime-" + role,
                active_role="NOVA_WORKER",
                principal_type="SERVICE",
            )
        )
        async with databases[role].transaction(actor) as connection:
            yield connection

    try:
        yield transaction
    finally:
        for db in databases.values():
            await db.close()


async def call(jobs, method, *args, role="task", identity=None, **kwargs):
    async with jobs(role, identity) as connection:
        return await getattr(JobRepository(connection), method)(*args, **kwargs)


async def enqueue(jobs, **changes):
    return await call(jobs, "enqueue", definition(**changes), role="web")


async def claim(jobs, *, seconds=60):
    rows = await call(jobs, "claim", "task", uuid4(), seconds=seconds)
    assert len(rows) == 1
    return rows[0]


async def advance(jobs, method, row, **kwargs):
    return await call(jobs, method, JobLease.from_record(row), **kwargs)


async def prepare(jobs, row):
    return await advance(jobs, "prepare", row, participant="starrocks", request_digest="b" * 64)


async def test_submission_and_outbox_are_atomic_and_dedupe_is_permanent(jobs):
    spec = definition()
    with pytest.raises(RuntimeError):
        async with jobs() as connection:
            await JobRepository(connection).enqueue(spec)
            raise RuntimeError("abort owning domain transaction")
    async with jobs() as connection:
        assert not await (await connection.execute("SELECT * FROM nova_meta.job_queue")).fetchall()
        assert not await (
            await connection.execute("SELECT * FROM nova_meta.outbox_events")
        ).fetchall()

    async def submit():
        return await call(jobs, "enqueue", spec, role="web")

    # Initialize the pool before concurrent callers, just as process startup does.
    first = await submit()
    results = await asyncio.gather(*(submit() for _ in range(12)))
    assert {row["id"] for row in results} == {first["id"]}
    with pytest.raises(MetadataConflict):
        await call(jobs, "enqueue", replace(spec, definition_digest="d" * 64), role="web")
    async with jobs() as connection:
        events = await (
            await connection.execute("SELECT * FROM nova_meta.outbox_events")
        ).fetchall()
        assert len(events) == 1 and events[0]["job_id"] == first["id"]
        assert first["owner_principal"] == "alice" and first["owner_role"] == "ANALYST"
        assert first["security_context_version"] == 1 and first["principal_type"] == "USER"
        with pytest.raises(Exception) as caught:
            async with connection.transaction():
                await connection.execute("DELETE FROM nova_meta.job_queue")
        assert caught.value.sqlstate == "42501"


@pytest.mark.parametrize("other", [user("bob"), user(role="OTHER"), user(version=2)])
async def test_actor_visibility_and_cross_actor_duplicate_refusal(jobs, other):
    spec = definition()
    row = await call(jobs, "enqueue", spec, role="web")
    assert await call(jobs, "get", row["id"], role="web", identity=other) is None
    with pytest.raises(MetadataConflict):
        await call(jobs, "enqueue", spec, role="web", identity=other)
    assert await call(jobs, "get", row["id"], role="agent") is None
    assert not await call(jobs, "claim", "task", uuid4(), role="agent")
    assert not await call(jobs, "claim", "task", uuid4(), identity=user())


async def test_competing_claims_and_stale_revision_have_one_winner(jobs):
    await enqueue(jobs)
    async with jobs("task"):
        pass
    results = await asyncio.gather(*(call(jobs, "claim", "task", uuid4()) for _ in range(12)))
    winners = [row for rows in results for row in rows]
    assert len(winners) == 1
    row = winners[0]
    assert row["attempt"] == 1 and row["lease_token"] == 1 and row["state"] == "LEASED"
    updated = await advance(jobs, "heartbeat", row)
    with pytest.raises(MetadataConflict):
        await prepare(jobs, row)
    prepared = await prepare(jobs, updated)
    assert prepared["state"] == "PREPARED"


async def test_skip_locked_does_not_wait_for_another_workers_row(jobs):
    one = await enqueue(jobs)
    two = await enqueue(jobs)
    async with jobs("task") as connection:
        await connection.execute(
            "SELECT id FROM nova_meta.job_queue WHERE id=%s FOR UPDATE", (one["id"],)
        )
        async with asyncio.timeout(2):
            taken = await claim(jobs)
        assert taken["id"] == two["id"]


@pytest.mark.parametrize(
    "phase", ["LEASED", "PREPARED", "SUBMITTED", "VERIFYING", "BLOCKED", "COMMITTED"]
)
async def test_expired_workers_cannot_repeat_armed_effects(jobs, phase):
    await enqueue(jobs)
    row = await claim(jobs, seconds=2)
    if phase != "LEASED":
        row = await prepare(jobs, row)
    if phase in {"SUBMITTED", "VERIFYING", "BLOCKED", "COMMITTED"}:
        row = await advance(jobs, "submitted", row)
    if phase in {"VERIFYING", "BLOCKED", "COMMITTED"}:
        row = await advance(jobs, "verifying", row)
    if phase == "BLOCKED":
        row = await advance(jobs, "block", row, error="PARTICIPANT_UNREACHABLE")
    if phase == "COMMITTED":
        row = await advance(
            jobs, "record_outcome", row, proof=EffectProof(uuid4(), "c" * 64), visible=True
        )
    await asyncio.sleep(2.05)
    successor = await claim(jobs)
    expected = (
        "LEASED" if phase == "LEASED" else "COMMITTED" if phase == "COMMITTED" else "VERIFYING"
    )
    assert successor["state"] == expected and successor["lease_token"] == 2
    assert successor["attempt"] == (2 if phase == "LEASED" else 1)
    with pytest.raises(MetadataConflict):
        await advance(jobs, "heartbeat", row)
    if phase != "LEASED":
        with pytest.raises(MetadataConflict):
            await prepare(jobs, successor)
        with pytest.raises(MetadataConflict):
            await advance(jobs, "submitted", successor)
        intent = await call(jobs, "intent", successor["id"], successor["attempt"])
        assert intent["request_digest"] == "b" * 64


async def test_verified_receipt_and_completion_survive_lost_ack(jobs):
    spec = definition()
    await call(jobs, "enqueue", spec, role="web")
    row = await prepare(jobs, await claim(jobs))
    row = await advance(jobs, "verifying", row)
    proof = EffectProof(uuid4(), "c" * 64)
    row = await advance(jobs, "record_outcome", row, proof=proof, visible=True)
    completed = await advance(jobs, "complete", row)
    recovered = await call(jobs, "enqueue", spec, role="web")
    assert recovered == completed and recovered["state"] == "COMPLETED"
    assert not await call(jobs, "claim", "task", uuid4())
    with pytest.raises(MetadataConflict):
        await advance(jobs, "complete", row)
    async with jobs("task") as connection:
        receipts = await (
            await connection.execute("SELECT * FROM nova_meta.external_effect_receipts")
        ).fetchall()
        assert len(receipts) == 1 and receipts[0]["evidence_ref"] == proof.evidence_ref


async def test_unknown_effect_blocks_same_resource_and_cannot_retry(jobs):
    resource = "d" * 64
    await enqueue(jobs, resource_key=resource)
    first = await prepare(jobs, await claim(jobs))
    first = await advance(jobs, "verifying", first)
    first = await advance(jobs, "block", first, error="OUTCOME_UNKNOWN")
    with pytest.raises(MetadataConflict):
        await call(jobs, "retry", first["id"], first["revision"], delay_seconds=1)
    await enqueue(jobs, resource_key=resource)
    second = await claim(jobs)
    with pytest.raises(MetadataConflict):
        await prepare(jobs, second)
    assert (await call(jobs, "get", second["id"]))["state"] == "LEASED"


async def test_retry_requires_proven_absence_and_preserves_prior_intent(jobs):
    await enqueue(jobs)
    first = await prepare(jobs, await claim(jobs))
    first = await advance(jobs, "verifying", first)
    failed = await advance(
        jobs,
        "record_outcome",
        first,
        proof=EffectProof(uuid4(), "c" * 64),
        visible=False,
        error="REMOTE_REJECTED",
        retryable=True,
    )
    pending = await call(jobs, "retry", failed["id"], failed["revision"], delay_seconds=1)
    assert pending["state"] == "PENDING" and pending["lease_token"] == 1
    assert not await call(jobs, "claim", "task", uuid4())
    await asyncio.sleep(1.05)
    second = await prepare(jobs, await claim(jobs))
    assert second["attempt"] == 2 and second["lease_token"] == 2
    first_intent = await call(jobs, "intent", first["id"], 1)
    second_intent = await call(jobs, "intent", first["id"], 2)
    assert first_intent["operation_id"] != second_intent["operation_id"]
    async with jobs("task") as connection:
        events = await (
            await connection.execute("SELECT * FROM nova_meta.outbox_events")
        ).fetchall()
        assert len(events) == 2


async def test_internal_work_cannot_claim_external_proof_shortcut(jobs):
    await enqueue(jobs)
    external = await claim(jobs)
    with pytest.raises(MetadataConflict):
        await advance(jobs, "commit_internal", external)
    await enqueue(jobs, external_effect=False)
    internal = await claim(jobs)
    with pytest.raises(MetadataConflict):
        await prepare(jobs, internal)
    committed = await advance(jobs, "commit_internal", internal)
    assert (await advance(jobs, "complete", committed))["state"] == "COMPLETED"


async def test_cancel_and_internal_failure_cannot_hide_an_armed_effect(jobs):
    await enqueue(jobs)
    row = await claim(jobs)
    with pytest.raises(MetadataConflict):
        await advance(jobs, "fail_internal", row, error="LOCAL_FAILURE")
    assert (await advance(jobs, "cancel_unprepared", row))["state"] == "CANCELLED"
    await enqueue(jobs)
    armed = await prepare(jobs, await claim(jobs))
    with pytest.raises(MetadataConflict):
        await advance(jobs, "cancel_unprepared", armed)
    await enqueue(jobs, external_effect=False)
    failed = await advance(jobs, "fail_internal", await claim(jobs), error="BAD_DEFINITION")
    assert failed["state"] == "FAILED" and not failed["retryable"]
    with pytest.raises(MetadataConflict):
        await call(jobs, "retry", failed["id"], failed["revision"], delay_seconds=1)


async def test_worker_cannot_dedupe_another_actors_submission(jobs):
    spec = definition()
    await call(jobs, "enqueue", spec, role="web")
    with pytest.raises(MetadataConflict):
        await call(jobs, "enqueue", spec)


async def test_database_rejects_pending_job_without_outbox(jobs):
    with pytest.raises(MetadataIntegrityViolation):
        async with jobs() as connection:
            await connection.execute(
                "INSERT INTO nova_meta.job_queue "
                "(id,queue,kind,definition_ref,definition_version,definition_digest,"
                "idempotency_key,resource_key,external_effect) "
                "VALUES (%s,'task','sql.execute',%s,1,%s,%s,%s,true)",
                (uuid4(), uuid4(), "a" * 64, uuid4(), "b" * 64),
            )


async def test_receipt_and_outcome_rollback_with_the_domain_transaction(jobs):
    await enqueue(jobs)
    row = await prepare(jobs, await claim(jobs))
    row = await advance(jobs, "verifying", row)
    with pytest.raises(RuntimeError):
        async with jobs("task") as connection:
            await JobRepository(connection).record_outcome(
                JobLease.from_record(row),
                EffectProof(uuid4(), "e" * 64),
                visible=True,
            )
            raise RuntimeError("owning domain update failed")
    assert (await call(jobs, "get", row["id"]))["state"] == "VERIFYING"
    async with jobs("task") as connection:
        cursor = await connection.execute("SELECT * FROM nova_meta.external_effect_receipts")
        assert not await cursor.fetchall()


async def test_outbox_concurrent_publishers_claim_each_event_once(jobs):
    for _ in range(5):
        await enqueue(jobs)
    async with jobs("outbox"):
        pass

    async def publish_claim():
        async with jobs("outbox") as connection:
            return await OutboxRepository(connection).claim(uuid4(), limit=1)

    attempts = await asyncio.gather(*(publish_claim() for _ in range(10)))
    events = [event for batch in attempts for event in batch]
    assert len(events) == 5 and len({event["id"] for event in events}) == 5


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE nova_meta.job_queue SET definition_digest=repeat('f',64)",
        "DELETE FROM nova_meta.external_effect_intents",
        "TRUNCATE nova_meta.job_queue CASCADE",
        "UPDATE nova_meta.external_effect_receipts SET result_digest=repeat('e',64)",
    ],
)
async def test_workloads_cannot_mutate_definition_or_erase_effect_history(jobs, statement):
    await enqueue(jobs)
    await prepare(jobs, await claim(jobs))
    with pytest.raises(MetadataPermissionDenied):
        async with jobs("task") as connection:
            await connection.execute(statement)


@pytest.mark.parametrize("target", ["COMMITTED", "COMPLETED", "FAILED", "PENDING"])
async def test_direct_state_jump_cannot_bypass_receipts(jobs, target):
    await enqueue(jobs)
    row = await prepare(jobs, await claim(jobs))
    row = await advance(jobs, "verifying", row)
    with pytest.raises(MetadataIntegrityViolation):
        async with jobs("task") as connection:
            await connection.execute(
                "UPDATE nova_meta.job_queue SET state=%s,revision=revision+1 WHERE id=%s",
                (target, row["id"]),
            )
    assert (await call(jobs, "get", row["id"]))["state"] == "VERIFYING"


async def test_outbox_loss_reclaims_notification_but_not_job_effect(jobs):
    job = await enqueue(jobs)
    async with jobs("outbox") as connection:
        first = (await OutboxRepository(connection).claim(uuid4(), seconds=1))[0]
        assert OutboxRepository.notification(first) == {
            "event_id": str(first["id"]),
            "job_id": str(job["id"]),
        }
        assert set(OutboxRepository.notification(first)) == {"event_id", "job_id"}
    # Simulate publication followed by process death before ACK. Duplicate wakeup is safe.
    prepared = await prepare(jobs, await claim(jobs))
    await asyncio.sleep(1.05)
    async with jobs("outbox") as connection:
        second = (await OutboxRepository(connection).claim(uuid4()))[0]
        assert second["id"] == first["id"] and second["publish_attempt"] == 2
        with pytest.raises(MetadataConflict):
            await OutboxRepository(connection).delivered(first)
        await OutboxRepository(connection).delivered(second)
    async with jobs("outbox") as connection:
        assert not await OutboxRepository(connection).claim(uuid4())
    assert (await call(jobs, "get", job["id"]))["state"] == prepared["state"]
    assert not await call(jobs, "claim", "task", uuid4())


async def test_redis_unavailable_does_not_block_pg_polling(jobs):
    await enqueue(jobs)
    async with jobs("outbox") as connection:
        repo = OutboxRepository(connection)
        event = (await repo.claim(uuid4()))[0]
        await repo.defer(event, delay_seconds=1)
    assert (await claim(jobs))["state"] == "LEASED"
    await asyncio.sleep(1.05)
    async with jobs("outbox") as connection:
        assert len(await OutboxRepository(connection).claim(uuid4())) == 1


async def test_maintenance_and_transaction_lifetime_block_job_mutations(jobs, environment):  # noqa: F811
    async with jobs() as connection:
        repo = JobRepository(connection)
        outbox = OutboxRepository(connection)
    with pytest.raises(ValueError):
        await repo.enqueue(definition())
    with pytest.raises(ValueError):
        await repo.prepare(
            JobLease(uuid4(), uuid4(), 1, 1), participant="starrocks", request_digest="b" * 64
        )
    with pytest.raises(ValueError):
        await outbox.claim(uuid4())
    await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    with pytest.raises(MetadataReadOnlyMaintenance):
        await enqueue(jobs)
