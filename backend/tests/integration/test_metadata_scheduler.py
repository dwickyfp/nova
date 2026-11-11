"""Scheduler leadership and due-occurrence fencing on the existing PG fixture."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.common.durable_jobs import JobRepository
from app.core.metadata_db import (
    MetadataConflict,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.modules.task_orchestration.metadata_scheduler import (
    ScheduledOccurrence,
    SchedulerRepository,
)
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_jobs import definition, jobs, user  # noqa: F401

pytestmark = pytest.mark.metadata_pg


def occurrence(**changes):
    return replace(
        ScheduledOccurrence(
            root_task_id=uuid4().hex,
            scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
            graph_run_id=uuid4().hex,
            definition=definition(kind="task.graph", external_effect=False),
        ),
        **changes,
    )


async def control(transaction, method, *args, **kwargs):
    async with transaction("scheduler") as connection:
        return await getattr(SchedulerRepository(connection), method)(*args, **kwargs)


async def reserve(transaction, lease, due, identity=None):
    async with transaction("scheduler", identity or user()) as connection:
        return await SchedulerRepository(connection).reserve(lease, due)


async def test_concurrent_leaders_have_one_winner_and_tokens_never_reset(jobs):  # noqa: F811
    async with jobs("scheduler"):
        pass
    attempts = await asyncio.gather(
        *(control(jobs, "acquire", "tasks", uuid4()) for _ in range(10))
    )
    leaders = [lease for lease in attempts if lease]
    assert len(leaders) == 1 and leaders[0].token == 1
    leader = leaders[0]
    await control(jobs, "renew", leader)
    await control(jobs, "release", leader)
    successor = await control(jobs, "acquire", "tasks", leader.holder)
    assert successor.token == 2
    for method in ("renew", "release"):
        with pytest.raises(MetadataConflict):
            await control(jobs, method, leader)
    with pytest.raises(MetadataPermissionDenied):
        async with jobs("scheduler") as connection:
            await connection.execute("DELETE FROM nova_meta.scheduler_leases")


async def test_due_occurrence_reserves_one_job_and_outbox_under_original_actor(jobs):  # noqa: F811
    leader = await control(jobs, "acquire", "tasks", uuid4())
    due = occurrence()
    records = await asyncio.gather(*(reserve(jobs, leader, due) for _ in range(10)))
    assert len({row["job_id"] for row in records}) == 1
    async with jobs() as connection:
        job = await JobRepository(connection).get(records[0]["job_id"])
        assert job["owner_principal"] == "alice" and job["owner_role"] == "ANALYST"
        assert job["idempotency_key"] == due.idempotency_key
        assert job["kind"] == "task.graph" and not job["external_effect"]
        events = await (
            await connection.execute("SELECT * FROM nova_meta.outbox_events")
        ).fetchall()
        assert len(events) == 1
    shifted = replace(due, scheduled_at=due.scheduled_at.astimezone(timezone(timedelta(hours=7))))
    assert (await reserve(jobs, leader, shifted))["job_id"] == job["id"]


async def test_definition_version_distinguishes_occurrences_and_drift_is_conflict(jobs):  # noqa: F811
    leader = await control(jobs, "acquire", "tasks", uuid4())
    due = occurrence()
    first = await reserve(jobs, leader, due)
    for changed in (
        replace(due, definition=replace(due.definition, definition_digest="e" * 64)),
        replace(due, graph_run_id=uuid4().hex),
    ):
        with pytest.raises(MetadataConflict):
            await reserve(jobs, leader, changed)
    revised = replace(
        due, graph_run_id=uuid4().hex, definition=replace(due.definition, definition_version=2)
    )
    assert (await reserve(jobs, leader, revised))["job_id"] != first["job_id"]


async def test_future_and_expired_leaders_cannot_publish_occurrences(jobs):  # noqa: F811
    leader = await control(jobs, "acquire", "tasks", uuid4(), seconds=1)
    with pytest.raises(MetadataConflict):
        await reserve(
            jobs, leader, occurrence(scheduled_at=datetime.now(UTC) + timedelta(minutes=1))
        )
    await asyncio.sleep(1.05)
    with pytest.raises(MetadataConflict):
        await reserve(jobs, leader, occurrence())
    successor = await control(jobs, "acquire", "tasks", uuid4())
    assert successor.token == 2
    await reserve(jobs, successor, occurrence())


async def test_occurrence_cannot_commit_after_its_leader_deadline(jobs):  # noqa: F811
    leader = await control(jobs, "acquire", "tasks", uuid4(), seconds=1)
    due = occurrence()
    with pytest.raises(MetadataIntegrityViolation):
        async with jobs("scheduler", user()) as connection:
            await SchedulerRepository(connection).reserve(leader, due)
            await asyncio.sleep(1.05)
    async with jobs() as connection:
        assert not await (await connection.execute("SELECT * FROM nova_meta.job_queue")).fetchall()
    successor = await control(jobs, "acquire", "tasks", uuid4())
    assert (await reserve(jobs, successor, due))["lease_token"] == 2


async def test_occurrence_and_notification_roll_back_with_the_graph_run(jobs):  # noqa: F811
    leader = await control(jobs, "acquire", "tasks", uuid4())
    with pytest.raises(RuntimeError):
        async with jobs("scheduler", user()) as connection:
            await SchedulerRepository(connection).reserve(leader, occurrence())
            raise RuntimeError("graph-run insertion failed")
    async with jobs() as connection:
        for table in ("job_queue", "outbox_events", "scheduled_occurrences"):
            assert not await (
                await connection.execute(f"SELECT * FROM nova_meta.{table}")
            ).fetchall()


@pytest.mark.parametrize("other", [user("bob"), user(role="OTHER"), user(version=2)])
async def test_scheduled_context_cannot_cross_principal_role_or_version(jobs, other):  # noqa: F811
    leader = await control(jobs, "acquire", "tasks", uuid4())
    due = occurrence()
    await reserve(jobs, leader, due)
    async with jobs("web", other) as connection:
        assert not await (
            await connection.execute("SELECT * FROM nova_meta.scheduled_occurrences")
        ).fetchall()
    with pytest.raises(MetadataConflict):
        await reserve(jobs, leader, due, other)


@pytest.mark.parametrize("role", ["web", "task", "agent", "outbox"])
async def test_only_scheduler_login_can_acquire_leadership(jobs, role):  # noqa: F811
    with pytest.raises(MetadataPermissionDenied):
        async with jobs(role) as connection:
            await SchedulerRepository(connection).acquire("tasks", uuid4())


async def test_scheduler_user_context_can_publish_but_cannot_take_leadership(jobs):  # noqa: F811
    with pytest.raises(MetadataPermissionDenied):
        async with jobs("scheduler", user()) as connection:
            await SchedulerRepository(connection).acquire("tasks", uuid4())
    leader = await control(jobs, "acquire", "tasks", uuid4())
    with pytest.raises(MetadataPermissionDenied):
        async with jobs("scheduler", user()) as connection:
            await SchedulerRepository(connection).renew(leader)


async def test_repository_cannot_open_its_own_transaction_after_service_exit(jobs):  # noqa: F811
    async with jobs("scheduler") as connection:
        repo = SchedulerRepository(connection)
    with pytest.raises(ValueError):
        await repo.acquire("tasks", uuid4())
