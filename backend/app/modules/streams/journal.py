"""Immutable consumption receipts; late recovery cannot move offsets backwards."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict

from app.modules.streams.claims import MetadataExecutor
from app.modules.streams.consumption import Consumption, ConsumptionState
from app.modules.streams.schemas import ChangeCursor, StreamError, operation_id
from app.modules.streams.verification import CommitReceipt, TargetOutcome

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

    async def record(
        self, consume: Consumption, state: ConsumptionState, receipt: CommitReceipt | None = None
    ) -> None:
        if state in {
            ConsumptionState.TARGET_COMMITTED, ConsumptionState.OFFSET_COMMITTED,
        } and (receipt is None or receipt.outcome != TargetOutcome.VISIBLE):
            raise StreamError("STREAM_RECEIPT_INVALID", "Visible commit evidence is required")
        if state == ConsumptionState.FAILED and (
            receipt is None or receipt.outcome != TargetOutcome.FAILED
        ):
            raise StreamError("STREAM_RECEIPT_INVALID", "Failure evidence is required")
        event_id = operation_id(consume.consume_id, state)
        values = (
            event_id, consume.consume_id, consume.stream_id, consume.snapshot.after.epoch,
            consume.generation, consume.snapshot.after.sequence, consume.snapshot.through.sequence,
            consume.operation_digest, consume.target.database, consume.target.table,
            consume.target.label, consume.target.principal, str(state),
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
            "operation_digest,target_database,target_object,engine_label,principal,state "
            "FROM NOVA_SYSTEM.AUDIT_STREAM_CONSUMPTIONS WHERE event_id=%s LIMIT 2",
            (event_id,),
        )
        if len(readback["rows"]) != 1 or tuple(readback["rows"][0]) != values[1:-1]:
            raise StreamError(
                "STREAM_CONSUMPTION_BLOCKED", "Consumption event requires verification"
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
