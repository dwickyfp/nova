"""Immutable consumption receipts; late recovery cannot move offsets backwards."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from app.core.metadata_db import (
    MetadataConflict,
    MetadataIntegrityViolation,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
)
from app.modules.streams.claims import Claim
from app.modules.streams.consumption import Consumption, ConsumptionState
from app.modules.streams.control import MetadataScope, stream_metadata
from app.modules.streams.metadata_repository import EVENT_FACTS, OPERATION_FIELDS
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, StreamError
from app.modules.streams.verification import CommitReceipt, TargetIdentity, TargetOutcome

logger = logging.getLogger(__name__)

#: A fact the database refused or whose commit outcome is unknown: only an
#: exact readback in a new transaction can tell what is durable.
_UNVERIFIED = (
    MetadataConflict, MetadataIntegrityViolation, MetadataOutcomeUnknown,
    MetadataOperationFailed,
)


def _operation_values(consume: Consumption) -> tuple:
    """The prepared operation facts, in ``OPERATION_FIELDS`` order."""
    return (
        consume.consume_id, consume.stream_id, consume.generation, consume.snapshot.source_id,
        consume.snapshot.after.epoch, consume.snapshot.after.sequence,
        consume.snapshot.through.sequence,
        consume.snapshot.schema_version, consume.operation_digest, consume.target.database,
        consume.target.table, consume.target.label, consume.target.principal,
        consume.target.load_id, consume.target.transaction_id,
        consume.active_role, consume.security_context_version, consume.attempt,
        consume.decision_claim("0" * 64, "submit").operation_digest,
        consume.decision_claim("0" * 64, "cancel").operation_digest,
    )


def _operation(consume: Consumption) -> dict[str, Any]:
    return dict(zip(OPERATION_FIELDS, _operation_values(consume), strict=True))


def _row_values(row: dict[str, Any]) -> tuple:
    return tuple(row[field] for field in OPERATION_FIELDS)


class ConsumptionJournal:
    """The OFFSET_COMMITTED receipt is the authoritative offset fact.

    CONFIG_STREAMS may cache an offset only after reading these receipts. No
    observer is allowed to trust a cache that omits a pending generation.
    """

    def __init__(self, metadata: MetadataScope = stream_metadata) -> None:
        self._metadata = metadata

    async def prepare(self, consume: Consumption) -> None:
        values = _operation_values(consume)
        try:
            async with self._metadata() as repository:
                await repository.prepare_consumption(_operation(consume))
        except _UNVERIFIED:
            logger.debug("consumption preparation needs readback", exc_info=True)
        async with self._metadata(read_only=True) as repository:
            row = await repository.operation(consume.consume_id)
        try:
            verified = row is not None and _row_values(row) == values
        except (KeyError, TypeError):
            verified = False
        if not verified:
            raise StreamError("STREAM_CONSUMPTION_BLOCKED", "Operation preparation is unverified")
        await self.record(consume, ConsumptionState.PREPARED)

    async def winning_operation(self, claim: Claim) -> Consumption | None:
        async with self._metadata(read_only=True) as repository:
            row = await repository.winning_operation(claim.operation_digest)
        if row is None:
            return None
        try:
            consume = Consumption(
                row["consume_id"], row["stream_id"], int(row["generation"]),
                ChangeSnapshot(
                    row["source_id"], ChangeCursor(int(row["epoch"]), int(row["sequence_from"])),
                    ChangeCursor(int(row["epoch"]), int(row["sequence_to"])),
                    int(row["schema_version"]),
                ),
                TargetIdentity(
                    row["target_database"], row["target_object"], row["engine_label"],
                    row["principal"], row["target_load_id"], row["target_transaction_id"],
                ),
                row["operation_digest"], row["active_role"], row["security_context_version"],
                int(row["dispatch_attempt"] or 0),
            )
            if (
                _row_values(row) != _operation_values(consume)
                or claim.operation_digest
                not in (row["submission_digest"], row["cancellation_digest"])
                or consume.decision_claim("0" * 64, "submit").key != claim.key
            ):
                raise ValueError("Operation facts do not match the winner")
        except (KeyError, TypeError, ValueError, IndexError, StreamError):
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Durable operation facts are inconsistent"
            ) from None
        return consume

    @staticmethod
    def _facts(consume: Consumption) -> tuple:
        """The operation facts every event repeats, in ``EVENT_FACTS`` order."""
        return (
            consume.consume_id, consume.stream_id, consume.snapshot.after.epoch,
            consume.generation, consume.snapshot.after.sequence, consume.snapshot.through.sequence,
            consume.operation_digest, consume.target.database, consume.target.table,
            consume.target.label, consume.target.principal,
        )

    async def verified_receipt(self, consume: Consumption) -> CommitReceipt | None:
        async with self._metadata(read_only=True) as repository:
            rows = await repository.terminal_events(consume.consume_id)
        if not rows:
            return None
        receipts = []
        try:
            if len(rows) > 3:
                raise ValueError("Ambiguous terminal evidence")
            for row in rows:
                if tuple(row[field] for field in EVENT_FACTS) != self._facts(consume):
                    raise ValueError("Receipt operation mismatch")
                data = row["receipt"]
                receipt = CommitReceipt(**{**data, "outcome": TargetOutcome(data["outcome"])})
                expected = {TargetOutcome.FAILED} if row["state"] == "FAILED" else {
                    TargetOutcome.COMMITTED, TargetOutcome.VISIBLE,
                }
                if receipt.outcome not in expected:
                    raise ValueError("Receipt state mismatch")
                for identity in (receipt.load_id, receipt.transaction_id, receipt.sink_rows):
                    if identity is not None and (type(identity) is not int or identity < 0):
                        raise ValueError("Invalid receipt identity")
                receipts.append(receipt)
            if any(receipt != receipts[0] for receipt in receipts[1:]):
                raise ValueError("Conflicting terminal receipts")
        except (KeyError, TypeError, ValueError):
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Durable consumption receipt is inconsistent"
            ) from None
        return receipts[0]

    async def record(
        self, consume: Consumption, state: ConsumptionState, receipt: CommitReceipt | None = None
    ) -> None:
        if state in {
            ConsumptionState.TARGET_COMMITTED, ConsumptionState.OFFSET_COMMITTED,
        } and (
            receipt is None
            or receipt.outcome not in {TargetOutcome.COMMITTED, TargetOutcome.VISIBLE}
        ):
            raise StreamError("STREAM_RECEIPT_INVALID", "Verified commit evidence is required")
        if state == ConsumptionState.FAILED and (
            receipt is None or receipt.outcome != TargetOutcome.FAILED
        ):
            raise StreamError("STREAM_RECEIPT_INVALID", "Failure evidence is required")
        facts = dict(zip(EVENT_FACTS, self._facts(consume), strict=True))
        payload = None if receipt is None else {
            **asdict(receipt), "outcome": str(receipt.outcome),
        }
        # A refused or unacknowledged insert is settled only by the exact
        # durable readback below; never by the exception alone.
        try:
            async with self._metadata() as repository:
                await repository.record_event(facts, str(state), payload)
            return
        except _UNVERIFIED:
            logger.debug("consumption event needs readback", exc_info=True)
        async with self._metadata(read_only=True) as repository:
            row = await repository.event(consume.consume_id, str(state))
        if row is None or (
            tuple(row[field] for field in EVENT_FACTS) != self._facts(consume)
            or row["state"] != str(state)
        ):
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Consumption event requires verification"
            )
        if state in {
            ConsumptionState.TARGET_COMMITTED, ConsumptionState.OFFSET_COMMITTED,
            ConsumptionState.FAILED,
        } and row["receipt"] != payload:
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Consumption receipt requires verification"
            )

    async def advance(self, consume: Consumption, receipt: CommitReceipt) -> ChangeCursor:
        await self.record(consume, ConsumptionState.OFFSET_COMMITTED, receipt)
        return await self.committed_cursor(consume.stream_id, consume.snapshot.after)

    async def committed_cursor(self, stream_id: str, creation: ChangeCursor) -> ChangeCursor:
        """The committed offset, proven by an unbroken chain of receipts.

        Starting at the stream's creation cursor, each OFFSET_COMMITTED receipt
        must start exactly where the previous one ended. A gap, a receipt that
        overlaps another, or two receipts from the same offset is not resolved
        by taking a maximum: it blocks the stream.
        """
        async with self._metadata(read_only=True) as repository:
            sequence = await repository.committed_cursor(
                stream_id, creation.epoch, creation.sequence,
            )
        return ChangeCursor(creation.epoch, sequence)

    async def recent(self, stream_id: str, limit: int) -> list[dict]:
        """The newest consumption events of a stream, without receipts or SQL."""
        async with self._metadata(read_only=True) as repository:
            rows = await repository.recent_events(stream_id, int(limit))
        return [
            {
                "consume_id": row["consume_id"],
                "state": row["state"],
                "offset_from": int(row["sequence_from"]),
                "offset_to": int(row["sequence_to"]),
                "target": f"{row['target_database']}.{row['target_object']}",
                "principal": row["principal"],
                "at": row["created_at"],
            }
            for row in rows
        ]

    async def failed_attempts(self, stream_id: str, generation: int, start: int) -> int:
        """Proven failures from ``start``: the next attempt's arbitration slot."""
        async with self._metadata(read_only=True) as repository:
            return await repository.failed_attempts(stream_id, generation, start)
