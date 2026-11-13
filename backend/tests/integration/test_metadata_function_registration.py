"""Real PG registration claims/receipts with a controlled engine participant.

These tests do not replace patched-StarRocks or live-provider acceptance.
"""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio

from app.common.durable_jobs import JobRepository
from app.common.metadata_actors import service_actor
from app.modules.llm_functions.registration_worker import (
    JOB_KIND,
    AIFunctionRegistrationParticipant,
    EngineRefusal,
)
from tests.integration.test_metadata_ai_configuration import (
    configuration,  # noqa: F401
    create,
)
from tests.integration.test_metadata_ai_registry import registry  # noqa: F401
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401

pytestmark = pytest.mark.metadata_pg


@pytest_asyncio.fixture
async def participant_store(configuration):  # noqa: F811
    change = await create(configuration, system_prompt="fixture prompt")

    @asynccontextmanager
    async def transaction():
        async with configuration("maintenance", service_actor("maintenance")) as (_, _, connection):
            yield connection

    return transaction, change.registration_jobs[0]


async def claim(transaction, *, seconds=60, kind=JOB_KIND):
    async with transaction() as connection:
        return await JobRepository(connection).claim(
            "maintenance", uuid4(), seconds=seconds, kind=kind,
        )


class Engine:
    def __init__(self, outcome):
        self.outcome = outcome
        self.body = None
        self.applications = 0

    async def apply(self, rendered):
        self.applications += 1
        if self.outcome == "refused":
            raise EngineRefusal()
        if self.outcome == "unknown_before_commit":
            raise ConnectionError("synthetic connection loss")
        self.body = rendered.body
        if self.outcome == "wrong_prompt":
            self.body = self.body.replace("fixture prompt", "another prompt")
        if self.outcome == "lost_ack":
            raise ConnectionError("synthetic lost acknowledgment")
        if self.outcome == "cancel_after_commit":
            raise asyncio.CancelledError()

    async def stored_body(self, name):
        assert name == "AI_COMPLETE"
        return self.body


@pytest.mark.parametrize("outcome,state", [
    ("success", "COMPLETED"), ("lost_ack", "COMPLETED"), ("refused", "FAILED"),
    ("unknown_before_commit", "BLOCKED"), ("wrong_prompt", "BLOCKED"),
])
async def test_participant_commits_only_verified_effect(participant_store, outcome, state):
    transaction, submitted = participant_store
    engine = Engine(outcome)
    participant = AIFunctionRegistrationParticipant(engine=engine, transaction=transaction)
    claimed = (await claim(transaction))[0]
    assert claimed["id"] == submitted["id"]
    assert await participant.handle(claimed) == state
    async with transaction() as connection:
        saved = await JobRepository(connection).get(claimed["id"])
        receipts = await (await connection.execute(
            "SELECT * FROM nova_meta.external_effect_receipts WHERE job_id=%s", (saved["id"],),
        )).fetchall()
    assert saved["state"] == state and saved["attempt"] == 1
    assert len(receipts) == (1 if state in {"COMPLETED", "FAILED"} else 0)
    assert engine.applications == 1


async def test_recovered_submitted_job_observes_without_reapplying(participant_store):
    transaction, _ = participant_store
    engine = Engine("cancel_after_commit")
    participant = AIFunctionRegistrationParticipant(engine=engine, transaction=transaction)
    claimed = (await claim(transaction, seconds=1))[0]
    with pytest.raises(asyncio.CancelledError):
        await participant.handle(claimed)
    async with transaction() as connection:
        assert (await JobRepository(connection).get(claimed["id"]))["state"] == "SUBMITTED"
    await asyncio.sleep(1.05)
    recovered = (await claim(transaction))[0]
    assert recovered["state"] == "VERIFYING" and recovered["attempt"] == 1
    assert await participant.handle(recovered) == "COMPLETED"
    assert engine.applications == 1


async def test_kind_filter_does_not_consume_another_participants_work(participant_store):
    transaction, _ = participant_store
    assert await claim(transaction, kind="another.participant") == []
    assert len(await claim(transaction)) == 1
