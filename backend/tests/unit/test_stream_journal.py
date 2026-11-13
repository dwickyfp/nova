from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.core.metadata_db import MetadataConflict, MetadataOutcomeUnknown
from app.modules.streams.consumption import Consumption, ConsumptionState
from app.modules.streams.journal import ConsumptionJournal, _operation_values
from app.modules.streams.metadata_repository import EVENT_FACTS, OPERATION_FIELDS
from app.modules.streams.schemas import ChangeCursor, ChangeSnapshot, StreamError, operation_id
from app.modules.streams.verification import CommitReceipt, TargetIdentity, TargetOutcome
from tests.unit.stream_metadata_fakes import metadata_scope

CONSUME = Consumption(
    operation_id("consume"),
    "stream",
    1,
    ChangeSnapshot("source", ChangeCursor(1, 0), ChangeCursor(1, 2), 1),
    TargetIdentity("d", "t", "nova_label", "alice"),
    operation_id("SQL"),
)
RECEIPT = CommitReceipt(TargetOutcome.COMMITTED, "verified_transaction", 31, None, 42)


def terminal_row(state="TARGET_COMMITTED", receipt=RECEIPT):
    facts = (
        CONSUME.consume_id,
        "stream",
        1,
        1,
        0,
        2,
        CONSUME.operation_digest,
        "d",
        "t",
        "nova_label",
        "alice",
    )
    return {
        **dict(zip(EVENT_FACTS, facts, strict=True)),
        "state": state,
        "receipt": asdict(receipt),
    }


async def test_saved_commit_survives_engine_evidence_expiry_without_target_access():
    scope, repository = metadata_scope()
    repository.terminal_events.return_value = [terminal_row()]
    journal = ConsumptionJournal(scope)
    assert await journal.verified_receipt(CONSUME) == RECEIPT
    repository.terminal_events.assert_awaited_once_with(CONSUME.consume_id)
    assert scope.transactions == [True]


@pytest.mark.parametrize(
    "fault", ["range", "principal", "receipt", "state", "conflict", "overflow"]
)
async def test_inconsistent_terminal_evidence_blocks_recovery(fault):
    row = terminal_row()
    if fault == "range":
        row["sequence_to"] = 9
    elif fault == "principal":
        row["principal"] = "other"
    elif fault == "receipt":
        row["receipt"] = "private-corrupt-payload"
    elif fault == "state":
        row["state"] = "FAILED"
    rows = [row]
    if fault == "conflict":
        rows.append(terminal_row("FAILED", CommitReceipt(TargetOutcome.FAILED, "verified_failed")))
    if fault == "overflow":
        rows *= 4
    scope, repository = metadata_scope()
    repository.terminal_events.return_value = rows
    journal = ConsumptionJournal(scope)
    with pytest.raises(StreamError, match="inconsistent") as raised:
        await journal.verified_receipt(CONSUME)
    assert "private" not in str(raised.value)


async def test_record_refuses_conflicting_receipt_even_when_operation_facts_match():
    row = terminal_row(
        receipt=CommitReceipt(TargetOutcome.VISIBLE, "verified_visible", 999, 0, 888)
    )
    scope, repository = metadata_scope()
    repository.record_event.side_effect = MetadataConflict()
    repository.event.return_value = row
    journal = ConsumptionJournal(scope)
    with pytest.raises(StreamError, match="receipt requires verification"):
        await journal.record(CONSUME, ConsumptionState.TARGET_COMMITTED, RECEIPT)


async def test_record_exact_commit_receipt_is_idempotent_after_lost_response():
    scope, repository = metadata_scope()
    repository.record_event.side_effect = MetadataOutcomeUnknown()
    repository.event.return_value = terminal_row()
    journal = ConsumptionJournal(scope)
    await journal.record(CONSUME, ConsumptionState.TARGET_COMMITTED, RECEIPT)


async def test_prepare_requires_exact_facts_before_publishing_prepared_event():
    scope, repository = metadata_scope()
    repository.prepare_consumption.side_effect = MetadataOutcomeUnknown()
    repository.operation.return_value = dict(
        zip(OPERATION_FIELDS, _operation_values(CONSUME), strict=True)
    )
    journal = ConsumptionJournal(scope)
    journal.record = AsyncMock()
    await journal.prepare(CONSUME)
    journal.record.assert_awaited_once_with(CONSUME, ConsumptionState.PREPARED)


@pytest.mark.parametrize("fault", ["missing", "malformed", "principal", "range"])
async def test_prepare_does_not_publish_unverified_operation(fault):
    row = dict(zip(OPERATION_FIELDS, _operation_values(CONSUME), strict=True))
    if fault == "principal":
        row["principal"] = "other"
    elif fault == "range":
        row["sequence_to"] += 1
    elif fault == "malformed":
        del row["submission_digest"]
    scope, repository = metadata_scope()
    repository.operation.return_value = None if fault == "missing" else row
    journal = ConsumptionJournal(scope)
    journal.record = AsyncMock()
    with pytest.raises(StreamError, match="preparation is unverified"):
        await journal.prepare(CONSUME)
    journal.record.assert_not_awaited()


@pytest.mark.parametrize("decision", ["submit", "cancel"])
async def test_cold_recovery_reconstructs_complete_winning_operation(decision):
    scope, repository = metadata_scope()
    repository.winning_operation.return_value = dict(
        zip(OPERATION_FIELDS, _operation_values(CONSUME), strict=True)
    )
    journal = ConsumptionJournal(scope)
    claim = CONSUME.decision_claim(operation_id("new worker"), decision)
    assert await journal.winning_operation(claim) == CONSUME


@pytest.mark.parametrize("column", [1, 3, 6, 8, 12, 15, 16, 17, 18])
async def test_winner_reconstruction_rejects_altered_persisted_facts(column):
    row = list(_operation_values(CONSUME))
    row[column] = row[column] + 1 if type(row[column]) is int else "altered"
    scope, repository = metadata_scope()
    repository.winning_operation.return_value = dict(zip(OPERATION_FIELDS, row, strict=True))
    journal = ConsumptionJournal(scope)
    with pytest.raises(StreamError, match="facts are inconsistent"):
        await journal.winning_operation(CONSUME.decision_claim(operation_id("worker"), "submit"))
