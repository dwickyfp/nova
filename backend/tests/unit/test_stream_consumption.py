import asyncio
from dataclasses import replace

import pytest

from app.modules.streams.claims import ClaimOutcome, ClaimResult
from app.modules.streams.consumption import Consumption, ConsumptionState, StreamConsumptionService
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, StreamError, operation_id
from app.modules.streams.verification import CommitReceipt, TargetIdentity, TargetOutcome


class Claims:
    def __init__(self):
        self.winner = None
        self.winners = {}

    async def acquire(self, claim):
        if claim.key in self.winners:
            return ClaimResult(ClaimOutcome.EXISTING, self.winners[claim.key])
        self.winner = claim
        self.winners[claim.key] = claim
        return ClaimResult(ClaimOutcome.ACQUIRED, claim)

    async def inspect(self, claim):
        winner = self.winners.get(claim.key)
        return ClaimResult(
            ClaimOutcome.EXISTING if winner else ClaimOutcome.VERIFICATION_REQUIRED, winner
        )


class Journal:
    def __init__(self):
        self.events = []
        self.offsets = set()
        self.receipts = {}
        self.operations = {}

    async def prepare(self, consume):
        self.operations[consume.consume_id] = consume

    async def winning_operation(self, claim):
        return next((
            consume for consume in self.operations.values()
            if claim.operation_digest in {
                consume.decision_claim("0" * 64, decision).operation_digest
                for decision in ("submit", "cancel")
            }
        ), None)

    async def record(self, consume, state, receipt=None):
        self.events.append(state)
        if receipt is not None and state in {
            ConsumptionState.TARGET_COMMITTED, ConsumptionState.OFFSET_COMMITTED,
            ConsumptionState.FAILED,
        }:
            self.receipts[consume.consume_id] = receipt

    async def verified_receipt(self, consume):
        return self.receipts.get(consume.consume_id)

    async def advance(self, consume, receipt):
        assert receipt.outcome in {TargetOutcome.COMMITTED, TargetOutcome.VISIBLE}
        self.offsets.add(consume.snapshot.through)
        return consume.snapshot.through


class Verifier:
    def __init__(self, outcome):
        self.outcome = outcome

    async def verify(self, target):
        return CommitReceipt(self.outcome, "test", 10, 0)


def scenario(outcome=TargetOutcome.VISIBLE):
    consume = Consumption(
        operation_id("consume"), "stream", 1,
        ChangeSnapshot("source", ChangeCursor(1, 0), ChangeCursor(1, 2), 1),
        TargetIdentity("d", "t", "nova_label", "alice"), operation_id("operation"),
    )
    journal = Journal()
    verifier = Verifier(outcome)
    service = StreamConsumptionService(Claims(), journal, verifier)
    return consume, service, journal, verifier


@pytest.mark.parametrize("decision", ["submit", "cancel"])
async def test_new_worker_recovers_winner_without_original_request(decision):
    consume, service, journal, verifier = scenario()
    await journal.prepare(consume)
    claim = consume.decision_claim(operation_id("original worker"), decision)
    await service.claims.acquire(claim)
    replacement = StreamConsumptionService(service.claims, journal, verifier)
    expected = (
        ConsumptionState.OFFSET_COMMITTED if decision == "submit" else ConsumptionState.CANCELLED
    )
    assert await replacement.recover_claim(claim) == expected
    assert bool(journal.offsets) == (decision == "submit")


async def test_legacy_claim_without_operation_facts_stays_blocked():
    consume, service, journal, _ = scenario()
    claim = consume.decision_claim(operation_id("old worker"), "submit")
    await service.claims.acquire(claim)
    with pytest.raises(StreamError, match="Operation facts are unavailable"):
        await service.recover_claim(claim)
    assert journal.events == []
    assert not journal.offsets


@pytest.mark.parametrize("outcome", [TargetOutcome.COMMITTED, TargetOutcome.VISIBLE])
async def test_crash_after_commit_recovers_without_replay(outcome):
    consume, service, journal, _ = scenario(outcome)
    submissions = []

    async def submit(value):
        submissions.append(value)
        raise OSError("response lost after engine commit")

    result = await service.dispatch(consume, attempt_id=operation_id("worker"), submit=submit)
    assert result == ConsumptionState.OFFSET_COMMITTED
    await service.recover(consume)
    assert submissions == [consume]
    assert journal.offsets == {ChangeCursor(1, 2)}


async def test_unknown_outcome_keeps_cursor_and_fence():
    consume, service, journal, _ = scenario(TargetOutcome.VERIFICATION_REQUIRED)

    async def submit(value):
        raise OSError("unknown outcome")

    result = await service.dispatch(consume, attempt_id=operation_id("worker"), submit=submit)
    assert result == ConsumptionState.VERIFICATION_REQUIRED
    assert not journal.offsets
    assert service.claims.winner is not None


async def test_cancel_winner_prevents_late_worker_submission():
    consume, service, journal, _ = scenario()
    assert await service.cancel_prepared(consume, attempt_id=operation_id("recovery")) == (
        ConsumptionState.CANCELLED
    )

    async def submit(value):
        raise AssertionError("cancelled operation must not execute")

    assert await service.dispatch(consume, attempt_id=operation_id("late"), submit=submit) == (
        ConsumptionState.CANCELLED
    )
    assert not journal.offsets


async def test_recovery_cannot_cancel_worker_paused_before_send():
    consume, service, journal, verifier = scenario(TargetOutcome.VERIFICATION_REQUIRED)
    ready, resume = asyncio.Event(), asyncio.Event()

    async def submit(value):
        ready.set()
        await resume.wait()
        verifier.outcome = TargetOutcome.VISIBLE

    task = asyncio.create_task(
        service.dispatch(consume, attempt_id=operation_id("worker"), submit=submit)
    )
    await ready.wait()
    assert await service.cancel_prepared(consume, attempt_id=operation_id("recovery")) == (
        ConsumptionState.VERIFICATION_REQUIRED
    )
    assert not journal.offsets
    resume.set()
    assert await task == ConsumptionState.OFFSET_COMMITTED


async def test_verified_failure_does_not_advance():
    consume, service, journal, _ = scenario(TargetOutcome.FAILED)
    await service.claims.acquire(consume.decision_claim(operation_id("worker"), "submit"))
    assert await service.recover(consume) == ConsumptionState.FAILED
    assert not journal.offsets


async def test_different_operations_on_same_stream_generation_cannot_both_submit():
    first, service, _, _ = scenario()
    second = replace(
        first, consume_id=operation_id("second consume"),
        operation_digest=operation_id("different SQL"),
        target=replace(first.target, label="nova_second"),
    )
    submissions = []

    async def submit(value):
        submissions.append(value.consume_id)

    await service.dispatch(first, attempt_id=operation_id("first worker"), submit=submit)
    with pytest.raises(StreamError, match="verification"):
        await service.dispatch(second, attempt_id=operation_id("second worker"), submit=submit)
    assert submissions == [first.consume_id]


async def test_different_streams_have_independent_dispatch_claims():
    first, service, _, _ = scenario()
    second = replace(first, consume_id=operation_id("second"), stream_id="other-stream")
    submissions = []

    async def submit(value):
        submissions.append(value.stream_id)

    for consumption in (first, second):
        assert await service.dispatch(
            consumption, attempt_id=operation_id(consumption.stream_id), submit=submit,
        ) == ConsumptionState.OFFSET_COMMITTED
    assert submissions == [first.stream_id, second.stream_id]


@pytest.mark.parametrize(
    "changed", ["range", "target", "principal", "source", "schema", "role", "context"]
)
async def test_recovery_cannot_substitute_facts_for_winning_operation(changed):
    consume, service, journal, _ = scenario()
    await service.claims.acquire(consume.decision_claim(operation_id("worker"), "submit"))
    if changed == "range":
        consume = replace(consume, snapshot=replace(consume.snapshot, through=ChangeCursor(1, 9)))
    elif changed == "source":
        consume = replace(consume, snapshot=replace(consume.snapshot, source_id="other-source"))
    elif changed == "schema":
        consume = replace(consume, snapshot=replace(consume.snapshot, schema_version=99))
    elif changed == "target":
        consume = replace(consume, target=replace(consume.target, table="other-target"))
    elif changed == "role":
        consume = replace(consume, active_role="other-role")
    elif changed == "context":
        consume = replace(consume, security_context_version=99)
    else:
        consume = replace(consume, target=replace(consume.target, principal="other-user"))
    with pytest.raises(StreamError, match="durable winner"):
        await service.recover(consume)
    assert not journal.events
    assert not journal.offsets


async def test_recovery_without_durable_submission_claim_never_advances():
    consume, service, journal, _ = scenario()
    with pytest.raises(StreamError, match="durable winner"):
        await service.recover(consume)
    assert not journal.offsets


async def test_recovery_uses_saved_commit_after_engine_receipt_expiry():
    consume, service, journal, verifier = scenario()
    await service.claims.acquire(consume.decision_claim(operation_id("worker"), "submit"))
    await journal.record(
        consume, ConsumptionState.TARGET_COMMITTED,
        CommitReceipt(TargetOutcome.VISIBLE, "verified_visible", 10, 0, 42),
    )
    verifier.outcome = TargetOutcome.VERIFICATION_REQUIRED
    assert await service.recover(consume) == ConsumptionState.OFFSET_COMMITTED
    assert journal.offsets == {consume.snapshot.through}
