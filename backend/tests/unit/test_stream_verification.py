from dataclasses import asdict, replace

import pytest

from app.modules.streams.verification import (
    TargetCommitVerifier,
    TargetIdentity,
    TargetOutcome,
    interpret_receipt,
)

TARGET = TargetIdentity("sales", "orders", "nova_consume_example", "alice")
ROW = {
    "ID": 123,
    "DB_NAME": "sales",
    "TABLE_NAME": "orders",
    "LABEL": "nova_consume_example",
    "USER": "alice",
    "TYPE": "INSERT",
    "STATE": "FINISHED",
    "SINK_ROWS": 0,
}


def test_zero_rows_is_still_a_verified_commit():
    receipt = interpret_receipt(TARGET, [ROW])
    assert receipt.outcome == TargetOutcome.VISIBLE
    assert receipt.sink_rows == 0
    assert receipt.load_id == 123


@pytest.mark.parametrize("state", ["PENDING", "LOADING", "COMMITTED", "UNKNOWN", None])
def test_nonterminal_state_never_authorizes_cursor_advance(state):
    receipt = interpret_receipt(TARGET, [{**ROW, "STATE": state}])
    assert receipt.outcome == TargetOutcome.VERIFICATION_REQUIRED


def test_cancelled_load_without_transaction_evidence_remains_uncertain():
    assert interpret_receipt(TARGET, [{**ROW, "STATE": "CANCELLED"}]).outcome == (
        TargetOutcome.VERIFICATION_REQUIRED
    )


@pytest.mark.parametrize("rows", [[], [ROW, ROW]])
def test_missing_or_ambiguous_metadata_is_unknown(rows):
    assert interpret_receipt(TARGET, rows).outcome == TargetOutcome.VERIFICATION_REQUIRED


@pytest.mark.parametrize("field", ["DB_NAME", "TABLE_NAME", "LABEL", "USER", "TYPE"])
def test_identity_mismatch_is_unknown(field):
    assert interpret_receipt(TARGET, [{**ROW, field: "other"}]).outcome == (
        TargetOutcome.VERIFICATION_REQUIRED
    )


def test_reused_label_cannot_override_known_load_identity():
    assert interpret_receipt(replace(TARGET, load_id=456), [ROW]).outcome == (
        TargetOutcome.VERIFICATION_REQUIRED
    )


@pytest.mark.parametrize("field,value", [("ID", None), ("ID", -1), ("SINK_ROWS", -1)])
def test_invalid_evidence_is_unknown(field, value):
    assert interpret_receipt(TARGET, [{**ROW, field: value}]).outcome == (
        TargetOutcome.VERIFICATION_REQUIRED
    )


def test_receipt_drops_engine_secrets():
    receipt = interpret_receipt(TARGET, [{**ROW, "ERROR_MSG": "secret", "TRACKING_SQL": "secret"}])
    assert "secret" not in repr(asdict(receipt))


async def test_reader_is_bounded_parameterized_and_never_submits_dml():
    calls = []

    async def read(sql, params):
        calls.append((sql, params))
        return {"columns": list(ROW), "rows": [list(ROW.values())]}

    result = await TargetCommitVerifier(read).verify(TARGET)
    assert result.outcome == TargetOutcome.VISIBLE
    assert len(calls) == 1
    assert calls[0][0].startswith("SELECT ")
    assert "LIMIT 2" in calls[0][0]
    assert calls[0][1] == (TARGET.database, TARGET.label)


async def test_reader_error_is_redacted_and_unknown():
    async def read(sql, params):
        raise RuntimeError("private-storage-secret")

    result = await TargetCommitVerifier(read).verify(TARGET)
    assert result.outcome == TargetOutcome.VERIFICATION_REQUIRED
    assert "private-storage-secret" not in repr(result)


@pytest.mark.parametrize("job_state", ["CANCELLED", "LOADING"])
@pytest.mark.parametrize("state,outcome", [
    ("ABORTED", TargetOutcome.FAILED),
    ("COMMITTED", TargetOutcome.COMMITTED),
    ("VISIBLE", TargetOutcome.VISIBLE),
    ("PREPARE", TargetOutcome.VERIFICATION_REQUIRED),
])
async def test_transaction_evidence_controls_terminal_outcome(job_state, state, outcome):
    calls = []
    load = {**ROW, "STATE": job_state, "TRANSACTION_ID": 987}
    transaction = {
        "TransactionId": "987", "Label": TARGET.label,
        "LoadJobSourceType": "INSERT_STREAMING", "TransactionStatus": state,
        "Reason": "private-storage-secret",
    }

    async def read(sql, params):
        calls.append((sql, params))
        row = load if len(calls) == 1 else transaction
        return {"columns": list(row), "rows": [list(row.values())]}

    receipt = await TargetCommitVerifier(read).verify(TARGET)
    assert receipt.outcome == outcome
    assert receipt.load_id == 123
    assert receipt.transaction_id == 987
    assert calls[1] == ("SHOW TRANSACTION FROM `sales` WHERE ID = %s", (987,))
    assert "private-storage-secret" not in repr(receipt)


@pytest.mark.parametrize("transaction_id", [None, -1, "invalid", 456])
def test_known_transaction_identity_must_match(transaction_id):
    receipt = interpret_receipt(
        replace(TARGET, transaction_id=987), [{**ROW, "TRANSACTION_ID": transaction_id}]
    )
    assert receipt.outcome == TargetOutcome.VERIFICATION_REQUIRED


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "error", "label", "id", "source", "state"]
)
async def test_cancelled_load_does_not_release_fence_with_invalid_transaction(fault):
    load = {**ROW, "STATE": "CANCELLED", "TRANSACTION_ID": 987}
    transaction = {
        "TransactionId": "987", "Label": TARGET.label,
        "LoadJobSourceType": "INSERT_STREAMING", "TransactionStatus": "ABORTED",
    }
    for key, field in {"label": "Label", "id": "TransactionId", "source": "LoadJobSourceType",
                       "state": "TransactionStatus"}.items():
        if fault == key:
            transaction[field] = "other"

    async def read(sql, params):
        if sql.startswith("SELECT"):
            return {"columns": list(load), "rows": [list(load.values())]}
        if fault == "error":
            raise OSError("private-storage-secret")
        count = 0 if fault == "missing" else 2 if fault == "duplicate" else 1
        return {"columns": list(transaction), "rows": [list(transaction.values())] * count}

    receipt = await TargetCommitVerifier(read).verify(TARGET)
    assert receipt.outcome == TargetOutcome.VERIFICATION_REQUIRED
    assert "private-storage-secret" not in repr(receipt)
