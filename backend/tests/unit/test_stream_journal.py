import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.modules.streams.consumption import Consumption, ConsumptionState
from app.modules.streams.journal import StarRocksConsumptionJournal, _operation_values
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, StreamError, operation_id
from app.modules.streams.verification import CommitReceipt, TargetIdentity, TargetOutcome

CONSUME = Consumption(
    operation_id("consume"), "stream", 1,
    ChangeSnapshot("source", ChangeCursor(1, 0), ChangeCursor(1, 2), 1),
    TargetIdentity("d", "t", "nova_label", "alice"), operation_id("SQL"),
)
RECEIPT = CommitReceipt(TargetOutcome.COMMITTED, "verified_transaction", 31, None, 42)


def terminal_row(state="TARGET_COMMITTED", receipt=RECEIPT):
    return [
        CONSUME.consume_id, "stream", 1, 1, 0, 2, CONSUME.operation_digest,
        "d", "t", "nova_label", "alice", state, json.dumps(asdict(receipt)),
    ]


async def test_saved_commit_survives_engine_evidence_expiry_without_target_access():
    execute = AsyncMock(return_value={"rows": [terminal_row()]})
    journal = StarRocksConsumptionJournal(execute)
    assert await journal.verified_receipt(CONSUME) == RECEIPT
    sql, params = execute.call_args.args
    assert sql.startswith("SELECT")
    assert "AUDIT_STREAM_CONSUMPTIONS" in sql
    assert "LIMIT 4" in sql
    assert params == (CONSUME.consume_id,)


@pytest.mark.parametrize(
    "fault", ["range", "principal", "receipt", "state", "conflict", "overflow"]
)
async def test_inconsistent_terminal_evidence_blocks_recovery(fault):
    row = terminal_row()
    if fault == "range":
        row[5] = 9
    elif fault == "principal":
        row[10] = "other"
    elif fault == "receipt":
        row[12] = "private-corrupt-payload"
    elif fault == "state":
        row[11] = "FAILED"
    rows = [row]
    if fault == "conflict":
        rows.append(terminal_row("FAILED", CommitReceipt(TargetOutcome.FAILED, "verified_failed")))
    if fault == "overflow":
        rows *= 4
    journal = StarRocksConsumptionJournal(AsyncMock(return_value={"rows": rows}))
    with pytest.raises(StreamError, match="inconsistent") as raised:
        await journal.verified_receipt(CONSUME)
    assert "private" not in str(raised.value)


async def test_record_refuses_conflicting_receipt_even_when_operation_facts_match():
    row = terminal_row(
        receipt=CommitReceipt(TargetOutcome.VISIBLE, "verified_visible", 999, 0, 888)
    )
    execute = AsyncMock(side_effect=[{"affected": 0}, {"rows": [row]}])
    journal = StarRocksConsumptionJournal(execute)
    with pytest.raises(StreamError, match="receipt requires verification"):
        await journal.record(CONSUME, ConsumptionState.TARGET_COMMITTED, RECEIPT)


async def test_record_exact_commit_receipt_is_idempotent_after_lost_response():
    execute = AsyncMock(side_effect=[OSError("response lost"), {"rows": [terminal_row()]}])
    journal = StarRocksConsumptionJournal(execute)
    await journal.record(CONSUME, ConsumptionState.TARGET_COMMITTED, RECEIPT)


async def test_prepare_requires_exact_facts_before_publishing_prepared_event():
    execute = AsyncMock(side_effect=[OSError("lost ACK"), {"rows": [_operation_values(CONSUME)]}])
    journal = StarRocksConsumptionJournal(execute)
    journal.record = AsyncMock()
    await journal.prepare(CONSUME)
    journal.record.assert_awaited_once_with(CONSUME, ConsumptionState.PREPARED)


@pytest.mark.parametrize("fault", ["missing", "ambiguous", "principal", "range"])
async def test_prepare_does_not_publish_unverified_operation(fault):
    row = list(_operation_values(CONSUME))
    if fault == "principal":
        row[12] = "other"
    elif fault == "range":
        row[6] += 1
    rows = [] if fault == "missing" else [row] * (2 if fault == "ambiguous" else 1)
    journal = StarRocksConsumptionJournal(AsyncMock(side_effect=[{}, {"rows": rows}]))
    journal.record = AsyncMock()
    with pytest.raises(StreamError, match="preparation is unverified"):
        await journal.prepare(CONSUME)
    journal.record.assert_not_awaited()


@pytest.mark.parametrize("decision", ["submit", "cancel"])
async def test_cold_recovery_reconstructs_complete_winning_operation(decision):
    journal = StarRocksConsumptionJournal(
        AsyncMock(return_value={"rows": [_operation_values(CONSUME)]})
    )
    claim = CONSUME.decision_claim(operation_id("new worker"), decision)
    assert await journal.winning_operation(claim) == CONSUME


@pytest.mark.parametrize("column", [1, 3, 6, 8, 12, 15, 16, 17, 18])
async def test_winner_reconstruction_rejects_altered_persisted_facts(column):
    row = list(_operation_values(CONSUME))
    row[column] = row[column] + 1 if type(row[column]) is int else "altered"
    journal = StarRocksConsumptionJournal(AsyncMock(return_value={"rows": [row]}))
    with pytest.raises(StreamError, match="facts are inconsistent"):
        await journal.winning_operation(CONSUME.decision_claim(operation_id("worker"), "submit"))
