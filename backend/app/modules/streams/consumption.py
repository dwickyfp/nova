"""One-shot target dispatch; recovery verifies and never replays target SQL."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.modules.streams.claims import Claim, ClaimOutcome, ClaimRepository
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, StreamError, operation_id
from app.modules.streams.verification import (
    CommitReceipt,
    TargetCommitVerifier,
    TargetIdentity,
    TargetOutcome,
)


class ConsumptionState(StrEnum):
    PREPARED = "PREPARED"
    TARGET_SUBMITTED = "TARGET_SUBMITTED"
    TARGET_COMMITTED = "TARGET_COMMITTED"
    OFFSET_COMMITTED = "OFFSET_COMMITTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class Consumption:
    consume_id: str
    stream_id: str
    generation: int
    snapshot: ChangeSnapshot
    target: TargetIdentity
    operation_digest: str
    active_role: str | None = None
    security_context_version: int | None = None

    def __post_init__(self) -> None:
        for value in (self.consume_id, self.operation_digest):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("Consumption identities must be SHA-256 hex digests")
        if type(self.generation) is not int or not 0 <= self.generation < 2**63:
            raise ValueError("Consumption generation must be a nonnegative BIGINT")
        if self.active_role is not None and not self.active_role.strip():
            raise ValueError("An active role must be named")
        if self.security_context_version is not None and (
            type(self.security_context_version) is not int
            or not 1 <= self.security_context_version < 2**63
        ):
            raise ValueError("A security context version must be a positive BIGINT")

    @property
    def dispatch_resource(self) -> str:
        return operation_id("stream-dispatch", self.stream_id)

    def decision_claim(self, attempt_id: str, decision: str) -> Claim:
        if decision not in {"submit", "cancel"}:
            raise ValueError("Invalid dispatch decision")
        return Claim(
            self.dispatch_resource,
            self.snapshot.after.epoch,
            self.generation,
            attempt_id,
            operation_id(
                decision, self.consume_id, self.operation_digest, self.stream_id,
                self.generation, self.snapshot.source_id, self.snapshot.after.epoch,
                self.snapshot.after.sequence, self.snapshot.through.sequence,
                self.snapshot.schema_version, self.target.database, self.target.table,
                self.target.label, self.target.principal,
                self.target.load_id if self.target.load_id is not None else -1,
                self.target.transaction_id if self.target.transaction_id is not None else -1,
                self.active_role or "",
                self.security_context_version if self.security_context_version is not None else -1,
            ),
        )


class ConsumptionJournal(Protocol):
    """Persist transitions monotonically and idempotently before returning.

    advance records an immutable verified offset receipt; it must not perform
    a blind mutable cursor upsert that a late worker could move backwards.
    """

    async def record(
        self, consume: Consumption, state: ConsumptionState, receipt: CommitReceipt | None = None
    ) -> None: ...

    async def advance(self, consume: Consumption, receipt: CommitReceipt) -> ChangeCursor: ...

    async def verified_receipt(self, consume: Consumption) -> CommitReceipt | None: ...

    async def prepare(self, consume: Consumption) -> None: ...

    async def winning_operation(self, claim: Claim) -> Consumption | None: ...


SubmitTarget = Callable[[Consumption], Awaitable[None]]


class StreamConsumptionService:
    def __init__(
        self,
        claims: ClaimRepository,
        journal: ConsumptionJournal,
        verifier: TargetCommitVerifier,
    ) -> None:
        self.claims = claims
        self.journal = journal
        self.verifier = verifier

    async def dispatch(
        self, consume: Consumption, *, attempt_id: str, submit: SubmitTarget
    ) -> ConsumptionState:
        await self.journal.prepare(consume)
        decision = consume.decision_claim(attempt_id, "submit")
        result = await self.claims.acquire(decision)
        if result.outcome != ClaimOutcome.ACQUIRED:
            if result.winner is not None and result.winner.operation_digest == (
                consume.decision_claim(attempt_id, "cancel").operation_digest
            ):
                return ConsumptionState.CANCELLED
            raise StreamError("STREAM_CONSUMPTION_BLOCKED", "Consumption requires verification")
        # A durable submission decision precedes both the visible ledger state
        # and external execution. Failure here intentionally leaves a fence.
        await self.journal.record(consume, ConsumptionState.TARGET_SUBMITTED)
        try:
            await submit(consume)
        except Exception:
            # A driver error alone cannot distinguish rollback from lost ACK.
            await self.journal.record(consume, ConsumptionState.VERIFICATION_REQUIRED)
        return await self.recover(consume)

    async def cancel_prepared(self, consume: Consumption, *, attempt_id: str) -> ConsumptionState:
        await self.journal.prepare(consume)
        decision = consume.decision_claim(attempt_id, "cancel")
        result = await self.claims.acquire(decision)
        if (
            result.winner is not None
            and result.winner.operation_digest == decision.operation_digest
        ):
            await self.journal.record(consume, ConsumptionState.CANCELLED)
            return ConsumptionState.CANCELLED
        # A submit winner may be suspended immediately before sending SQL.
        # Missing engine metadata cannot prove that it will never submit.
        await self.journal.record(consume, ConsumptionState.VERIFICATION_REQUIRED)
        return ConsumptionState.VERIFICATION_REQUIRED

    async def recover(self, consume: Consumption) -> ConsumptionState:
        decision = consume.decision_claim("0" * 64, "submit")
        result = await self.claims.inspect(decision)
        if result.winner is None or result.winner.operation_digest != decision.operation_digest:
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Consumption facts do not match the durable winner"
            )
        receipt = await self.journal.verified_receipt(consume)
        if receipt is None:
            receipt = await self.verifier.verify(consume.target)
        if receipt.outcome in {TargetOutcome.COMMITTED, TargetOutcome.VISIBLE}:
            await self.journal.record(consume, ConsumptionState.TARGET_COMMITTED, receipt)
            cursor = await self.journal.advance(consume, receipt)
            if cursor.epoch != consume.snapshot.through.epoch or cursor.precedes(
                consume.snapshot.through
            ):
                raise StreamError("STREAM_CONSUMPTION_BLOCKED", "Offset receipt is inconsistent")
            await self.journal.record(consume, ConsumptionState.OFFSET_COMMITTED, receipt)
            return ConsumptionState.OFFSET_COMMITTED
        if receipt.outcome == TargetOutcome.FAILED:
            await self.journal.record(consume, ConsumptionState.FAILED, receipt)
            return ConsumptionState.FAILED
        await self.journal.record(consume, ConsumptionState.VERIFICATION_REQUIRED, receipt)
        return ConsumptionState.VERIFICATION_REQUIRED

    async def recover_claim(self, claim: Claim) -> ConsumptionState:
        """Recover from persisted winner facts without a live request or its SQL."""
        result = await self.claims.inspect(claim)
        if result.winner is None:
            raise StreamError("STREAM_CONSUMPTION_BLOCKED", "Submission claim is unavailable")
        winner = result.winner
        consume = await self.journal.winning_operation(winner)
        if consume is None:
            raise StreamError("STREAM_CONSUMPTION_BLOCKED", "Operation facts are unavailable")
        cancelled = consume.decision_claim("0" * 64, "cancel")
        if winner.key == cancelled.key and winner.operation_digest == cancelled.operation_digest:
            await self.journal.record(consume, ConsumptionState.CANCELLED)
            return ConsumptionState.CANCELLED
        return await self.recover(consume)
