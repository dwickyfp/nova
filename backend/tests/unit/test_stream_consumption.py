import asyncio

from app.modules.streams.claims import ClaimOutcome, ClaimResult
from app.modules.streams.consumption import Consumption, ConsumptionState, StreamConsumptionService
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, operation_id
from app.modules.streams.verification import CommitReceipt, TargetIdentity, TargetOutcome


class Claims:
    def __init__(self):
        self.winner = None

    async def acquire(self, claim):
        if self.winner:
            return ClaimResult(ClaimOutcome.EXISTING, self.winner)
        self.winner = claim
        return ClaimResult(ClaimOutcome.ACQUIRED, claim)


class Journal:
    def __init__(self):
        self.events = []
        self.offsets = set()

    async def record(self, consume, state, receipt=None):
        self.events.append(state)

    async def advance(self, consume, receipt):
        assert receipt.outcome == TargetOutcome.VISIBLE
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


async def test_crash_after_commit_recovers_without_replay():
    consume, service, journal, _ = scenario()
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
    assert await service.recover(consume) == ConsumptionState.FAILED
    assert not journal.offsets
