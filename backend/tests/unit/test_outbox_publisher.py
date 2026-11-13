"""Committed wakeups, bounded Redis failures, and cancellation keep durable work intact."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.metadata_db import (
    MetadataConflict,
    MetadataOutcomeUnknown,
    MetadataReadOnlyMaintenance,
)
from app.worker import outbox


@pytest.fixture
def fixture(monkeypatch):
    event = {
        "id": uuid4(),
        "job_id": uuid4(),
        "lease_holder": uuid4(),
        "lease_token": 1,
        "publish_attempt": 1,
        "unused_private_value": "not-for-transport",
    }
    store = SimpleNamespace(
        claim=AsyncMock(return_value=[event]),
        delivered=AsyncMock(),
        defer=AsyncMock(),
        notification=outbox.OutboxRepository.notification,
    )
    state = SimpleNamespace(open=False, commits=0, actors=[])

    @asynccontextmanager
    async def transaction(actor):
        assert not state.open
        state.open = True
        state.actors.append(actor)
        try:
            yield object(), actor
        finally:
            state.open = False
            state.commits += 1

    monkeypatch.setattr(outbox, "metadata_transaction", transaction)
    monkeypatch.setattr(outbox, "OutboxRepository", lambda _connection: store)
    # notification is a static projection, independent of the transaction double.
    monkeypatch.setattr(outbox.OutboxRepository, "notification", store.notification, raising=False)
    return store, event, state


async def test_publish_happens_after_claim_commit_and_sends_only_ids(fixture):
    store, event, state = fixture

    async def xadd(stream, fields, **options):
        assert not state.open and state.commits == 1
        assert fields == {"event_id": str(event["id"]), "job_id": str(event["job_id"])}
        assert options["maxlen"] == 10000

    worker = outbox.OutboxPublisher(SimpleNamespace(xadd=AsyncMock(side_effect=xadd)))
    assert await worker.publish_batch(asyncio.Event()) == 1
    store.delivered.assert_awaited_once_with(event)
    store.defer.assert_not_awaited()
    assert all(
        actor.principal_type == "SERVICE" and actor.principal == "nova-outbox"
        for actor in state.actors
    )


@pytest.mark.parametrize("failure", [RedisConnectionError(), TimeoutError()])
async def test_redis_failure_defers_without_claiming_or_executing_the_job(fixture, failure):
    store, event, _ = fixture
    worker = outbox.OutboxPublisher(SimpleNamespace(xadd=AsyncMock(side_effect=failure)))
    assert await worker.publish_batch(asyncio.Event()) == 0
    store.defer.assert_awaited_once_with(event, delay_seconds=2)
    store.delivered.assert_not_awaited()


async def test_unknown_ack_commit_is_not_retried_in_the_same_process(fixture):
    store, _, _ = fixture
    store.delivered.side_effect = MetadataOutcomeUnknown()
    client = SimpleNamespace(xadd=AsyncMock())
    worker = outbox.OutboxPublisher(client)
    with pytest.raises(MetadataOutcomeUnknown):
        await worker.publish_batch(asyncio.Event())
    client.xadd.assert_awaited_once()
    store.defer.assert_not_awaited()


async def test_expired_ack_does_not_overwrite_the_new_publishers_claim(fixture):
    store, _, _ = fixture
    store.delivered.side_effect = MetadataConflict()
    worker = outbox.OutboxPublisher(SimpleNamespace(xadd=AsyncMock()))
    assert await worker.publish_batch(asyncio.Event()) == 0
    store.defer.assert_not_awaited()


async def test_cancellation_leaves_claim_for_expiry_and_does_not_ack(fixture):
    store, _, _ = fixture
    started = asyncio.Event()

    async def block(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    worker = outbox.OutboxPublisher(SimpleNamespace(xadd=AsyncMock(side_effect=block)))
    task = asyncio.create_task(worker.publish_batch(asyncio.Event()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    store.delivered.assert_not_awaited()
    store.defer.assert_not_awaited()


async def test_maintenance_waits_and_shutdown_does_not_spin(fixture, monkeypatch):
    store, _, _ = fixture
    store.claim.side_effect = MetadataReadOnlyMaintenance()
    worker = outbox.OutboxPublisher(SimpleNamespace(xadd=AsyncMock()))
    stop = asyncio.Event()
    monkeypatch.setattr(outbox, "POLL_SECONDS", 0.01)
    task = asyncio.create_task(worker.run_forever(stop))
    while not store.claim.await_count:
        await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, 1)
    store.claim.assert_awaited_once()
