"""Immutable consumption receipts; late recovery cannot move offsets backwards."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict

from app.modules.streams.claims import Claim, MetadataExecutor
from app.modules.streams.consumption import Consumption, ConsumptionState
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, StreamError, operation_id
from app.modules.streams.verification import CommitReceipt, TargetIdentity, TargetOutcome

CONSUMPTIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS (
    event_id VARCHAR(64) NOT NULL,
    consume_id VARCHAR(64) NOT NULL,
    stream_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    generation BIGINT NOT NULL,
    sequence_from BIGINT NOT NULL,
    sequence_to BIGINT NOT NULL,
    operation_digest VARCHAR(64) NOT NULL,
    target_database VARCHAR(256) NOT NULL,
    target_object VARCHAR(256) NOT NULL,
    engine_label VARCHAR(128) NOT NULL,
    principal VARCHAR(256) NOT NULL,
    state VARCHAR(32) NOT NULL,
    receipt JSON,
    created_at DATETIME NOT NULL
) PRIMARY KEY(event_id)
DISTRIBUTED BY HASH(event_id) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

CONSUMPTION_OPERATIONS_DDL = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTION_OPERATIONS (
    consume_id VARCHAR(64) NOT NULL,
    stream_id VARCHAR(64) NOT NULL,
    generation BIGINT NOT NULL,
    source_id VARCHAR(64) NOT NULL,
    epoch BIGINT NOT NULL,
    sequence_from BIGINT NOT NULL,
    sequence_to BIGINT NOT NULL,
    schema_version BIGINT NOT NULL,
    operation_digest VARCHAR(64) NOT NULL,
    target_database VARCHAR(256) NOT NULL,
    target_object VARCHAR(256) NOT NULL,
    engine_label VARCHAR(128) NOT NULL,
    principal VARCHAR(256) NOT NULL,
    target_load_id BIGINT,
    target_transaction_id BIGINT,
    active_role VARCHAR(128),
    security_context_version BIGINT,
    submission_digest VARCHAR(64) NOT NULL,
    cancellation_digest VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(consume_id)
DISTRIBUTED BY HASH(consume_id) BUCKETS 1
PROPERTIES ('replication_num'='1')
"""

_OPERATION_COLUMNS = (
    "consume_id,stream_id,generation,source_id,epoch,sequence_from,sequence_to,"
    "schema_version,operation_digest,target_database,target_object,engine_label,principal,"
    "target_load_id,target_transaction_id,active_role,security_context_version,"
    "submission_digest,cancellation_digest"
)


def _operation_values(consume: Consumption) -> tuple:
    return (
        consume.consume_id, consume.stream_id, consume.generation, consume.snapshot.source_id,
        consume.snapshot.after.epoch, consume.snapshot.after.sequence,
        consume.snapshot.through.sequence,
        consume.snapshot.schema_version, consume.operation_digest, consume.target.database,
        consume.target.table, consume.target.label, consume.target.principal,
        consume.target.load_id, consume.target.transaction_id,
        consume.active_role, consume.security_context_version,
        consume.decision_claim("0" * 64, "submit").operation_digest,
        consume.decision_claim("0" * 64, "cancel").operation_digest,
    )


class StarRocksConsumptionJournal:
    """The OFFSET_COMMITTED receipt is the authoritative offset fact.

    CONFIG_STREAMS may cache an offset only after reading these receipts. No
    observer is allowed to trust a cache that omits a pending generation.
    """

    def __init__(self, execute: MetadataExecutor | None = None) -> None:
        if execute is None:
            from app.core.database import db

            execute = db.execute_system
        self._execute = execute

    async def prepare(self, consume: Consumption) -> None:
        values = _operation_values(consume)
        with suppress(Exception):
            await self._execute(
                "INSERT INTO NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTION_OPERATIONS "
                f"({_OPERATION_COLUMNS},created_at) "
                f"WITH LABEL nova_stream_prepared_{consume.consume_id} "
                "SELECT " + ",".join(["%s"] * len(values)) + ",NOW() "
                "WHERE NOT EXISTS (SELECT 1 FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTION_OPERATIONS "
                "WHERE consume_id=%s)",
                (*values, consume.consume_id),
            )
        result = await self._execute(
            f"SELECT {_OPERATION_COLUMNS} FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTION_OPERATIONS "
            "WHERE consume_id=%s LIMIT 2", (consume.consume_id,),
        )
        if len(result["rows"]) != 1 or tuple(result["rows"][0]) != values:
            raise StreamError("STREAM_CONSUMPTION_BLOCKED", "Operation preparation is unverified")
        await self.record(consume, ConsumptionState.PREPARED)

    async def winning_operation(self, claim: Claim) -> Consumption | None:
        result = await self._execute(
            f"SELECT {_OPERATION_COLUMNS} FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTION_OPERATIONS "
            "WHERE submission_digest=%s OR cancellation_digest=%s LIMIT 2",
            (claim.operation_digest, claim.operation_digest),
        )
        if not result["rows"]:
            return None
        try:
            if len(result["rows"]) != 1:
                raise ValueError("Ambiguous operation facts")
            row = result["rows"][0]
            consume = Consumption(
                row[0], row[1], row[2],
                ChangeSnapshot(row[3], ChangeCursor(row[4], row[5]),
                               ChangeCursor(row[4], row[6]), row[7]),
                TargetIdentity(row[9], row[10], row[11], row[12], row[13], row[14]), row[8],
                row[15], row[16],
            )
            if (
                tuple(row) != _operation_values(consume)
                or claim.operation_digest not in row[17:19]
                or consume.decision_claim("0" * 64, "submit").key != claim.key
            ):
                raise ValueError("Operation facts do not match the winner")
        except (TypeError, ValueError, IndexError, StreamError):
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Durable operation facts are inconsistent"
            ) from None
        return consume

    @staticmethod
    def _facts(consume: Consumption) -> tuple:
        return (
            consume.consume_id, consume.stream_id, consume.snapshot.after.epoch,
            consume.generation, consume.snapshot.after.sequence, consume.snapshot.through.sequence,
            consume.operation_digest, consume.target.database, consume.target.table,
            consume.target.label, consume.target.principal,
        )

    async def verified_receipt(self, consume: Consumption) -> CommitReceipt | None:
        result = await self._execute(
            "SELECT consume_id,stream_id,epoch,generation,sequence_from,sequence_to,"
            "operation_digest,target_database,target_object,engine_label,principal,state,receipt "
            "FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS WHERE consume_id=%s "
            "AND state IN ('TARGET_COMMITTED','OFFSET_COMMITTED','FAILED') LIMIT 4",
            (consume.consume_id,),
        )
        if not result["rows"]:
            return None
        receipts = []
        try:
            if len(result["rows"]) > 3:
                raise ValueError("Ambiguous terminal evidence")
            for row in result["rows"]:
                if tuple(row[:11]) != self._facts(consume):
                    raise ValueError("Receipt operation mismatch")
                data = json.loads(row[12]) if isinstance(row[12], str) else row[12]
                receipt = CommitReceipt(**{**data, "outcome": TargetOutcome(data["outcome"])})
                expected = {TargetOutcome.FAILED} if row[11] == "FAILED" else {
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
        event_id = operation_id(consume.consume_id, state)
        values = (
            event_id, *self._facts(consume), str(state),
            None if receipt is None else json.dumps(asdict(receipt), sort_keys=True),
        )
        # Duplicate label or lost ACK: only exact durable readback below can
        # establish that this event exists. Never accept an exception alone.
        with suppress(Exception):
            await self._execute(
                "INSERT INTO NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS "
                f"WITH LABEL nova_stream_event_{event_id} "
                "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW() "
                "WHERE NOT EXISTS (SELECT 1 FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS "
                "WHERE event_id=%s)",
                (*values, event_id),
            )
        readback = await self._execute(
            "SELECT consume_id,stream_id,epoch,generation,sequence_from,sequence_to,"
            "operation_digest,target_database,target_object,engine_label,principal,state,receipt "
            "FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS WHERE event_id=%s LIMIT 2",
            (event_id,),
        )
        if len(readback["rows"]) != 1 or tuple(readback["rows"][0][:12]) != values[1:-1]:
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Consumption event requires verification"
            )
        if state in {
            ConsumptionState.TARGET_COMMITTED, ConsumptionState.OFFSET_COMMITTED,
            ConsumptionState.FAILED,
        }:
            try:
                persisted = readback["rows"][0][12]
                persisted = json.loads(persisted) if isinstance(persisted, str) else persisted
                matches = persisted == json.loads(values[-1])
            except (ValueError, TypeError, IndexError):
                matches = False
            if not matches:
                raise StreamError(
                    "STREAM_CONSUMPTION_BLOCKED", "Consumption receipt requires verification"
                )

    async def advance(self, consume: Consumption, receipt: CommitReceipt) -> ChangeCursor:
        await self.record(consume, ConsumptionState.OFFSET_COMMITTED, receipt)
        return await self.committed_cursor(consume.stream_id, consume.snapshot.after)

    async def committed_cursor(self, stream_id: str, creation: ChangeCursor) -> ChangeCursor:
        result = await self._execute(
            "SELECT MAX(sequence_to) FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS "
            "WHERE stream_id=%s AND epoch=%s AND state='OFFSET_COMMITTED'",
            (stream_id, creation.epoch),
        )
        sequence = result["rows"][0][0]
        return ChangeCursor(creation.epoch, max(creation.sequence, int(sequence or 0)))
