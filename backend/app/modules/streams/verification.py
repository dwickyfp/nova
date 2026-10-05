"""Interpret bounded engine evidence without replaying a data mutation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TargetOutcome(StrEnum):
    COMMITTED = "COMMITTED"
    VISIBLE = "VISIBLE"
    FAILED = "FAILED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    database: str
    table: str
    label: str
    principal: str
    load_id: int | None = None
    transaction_id: int | None = None


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    outcome: TargetOutcome
    reason: str
    load_id: int | None = None
    sink_rows: int | None = None
    transaction_id: int | None = None


def interpret_receipt(
    target: TargetIdentity, rows: Sequence[Mapping[str, Any]]
) -> CommitReceipt:
    """Only an exact, unambiguous terminal INSERT record establishes outcome.

    Load ID is not a transaction ID. Never copy arbitrary engine columns into
    a receipt: ERROR_MSG and tracking fields can contain execution credentials.
    """
    unknown = TargetOutcome.VERIFICATION_REQUIRED
    if len(rows) != 1:
        return CommitReceipt(unknown, "missing_or_ambiguous_evidence")
    row = rows[0]
    expected = {
        "DB_NAME": target.database,
        "TABLE_NAME": target.table,
        "LABEL": target.label,
        "USER": target.principal,
        "TYPE": "INSERT",
    }
    if any(row.get(key) != value for key, value in expected.items()):
        return CommitReceipt(unknown, "identity_mismatch")
    try:
        load_id = int(row["ID"])
        sink_rows = None if row.get("SINK_ROWS") is None else int(row["SINK_ROWS"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return CommitReceipt(unknown, "invalid_evidence")
    if load_id < 0 or (sink_rows is not None and sink_rows < 0):
        return CommitReceipt(unknown, "invalid_evidence")
    if target.load_id is not None and target.load_id != load_id:
        return CommitReceipt(unknown, "load_identity_mismatch")
    transaction_id = row.get("TRANSACTION_ID")
    if transaction_id is not None:
        try:
            transaction_id = int(transaction_id)
        except (TypeError, ValueError, OverflowError):
            return CommitReceipt(unknown, "invalid_transaction_identity")
        if transaction_id < 0:
            return CommitReceipt(unknown, "invalid_transaction_identity")
    if target.transaction_id is not None and target.transaction_id != transaction_id:
        return CommitReceipt(unknown, "transaction_identity_mismatch")
    state = row.get("STATE")
    if state == "FINISHED":
        return CommitReceipt(
            TargetOutcome.VISIBLE, "verified_visible", load_id, sink_rows, transaction_id
        )
    if state == "CANCELLED":
        # A cancelled load job alone cannot establish whether aborting its
        # transaction succeeded. The verifier must inspect the transaction.
        return CommitReceipt(
            unknown, "transaction_verification_required", load_id,
            transaction_id=transaction_id,
        )
    return CommitReceipt(
        unknown, "nonterminal_evidence", load_id, transaction_id=transaction_id
    )


MetadataReader = Callable[[str, tuple], Awaitable[dict]]


class TargetCommitVerifier:
    """Read only control-plane loading evidence using the existing DB adapter.

    Callers must authorize the operation before invoking verification. This
    adapter never reads target rows or executes the target statement.
    """

    def __init__(self, reader: MetadataReader | None = None) -> None:
        if reader is None:
            from app.core.database import db

            reader = db.execute_system
        self._read = reader

    async def verify(self, target: TargetIdentity) -> CommitReceipt:
        try:
            result = await self._read(
                "SELECT ID, LABEL, DB_NAME, TABLE_NAME, USER, TYPE, STATE, SINK_ROWS, "
                "CAST(RUNTIME_DETAILS->'txn_id' AS BIGINT) AS TRANSACTION_ID "
                "FROM information_schema.loads WHERE DB_NAME = %s AND LABEL = %s LIMIT 2",
                (target.database, target.label),
            )
            columns = result["columns"]
            rows = [dict(zip(columns, row, strict=True)) for row in result["rows"]]
        except Exception:  # Evidence unavailable is never permission to replay.
            return CommitReceipt(TargetOutcome.VERIFICATION_REQUIRED, "evidence_unavailable")
        receipt = interpret_receipt(target, rows)
        if receipt.outcome == TargetOutcome.VISIBLE or receipt.reason not in {
            "transaction_verification_required", "nonterminal_evidence",
        } or receipt.transaction_id is None:
            return receipt
        database = "`" + target.database.replace("`", "``") + "`"
        try:
            result = await self._read(
                f"SHOW TRANSACTION FROM {database} WHERE ID = %s", (receipt.transaction_id,)
            )
            transactions = [
                dict(zip(result["columns"], row, strict=True)) for row in result["rows"]
            ]
            if len(transactions) != 1:
                return CommitReceipt(TargetOutcome.VERIFICATION_REQUIRED, "transaction_unavailable")
            transaction = transactions[0]
            if (
                int(transaction["TransactionId"]) != receipt.transaction_id
                or transaction["Label"] != target.label
                or transaction["LoadJobSourceType"] != "INSERT_STREAMING"
            ):
                return CommitReceipt(
                    TargetOutcome.VERIFICATION_REQUIRED, "transaction_identity_mismatch"
                )
        except Exception:
            return CommitReceipt(TargetOutcome.VERIFICATION_REQUIRED, "transaction_unavailable")
        outcomes = {
            "VISIBLE": TargetOutcome.VISIBLE,
            "COMMITTED": TargetOutcome.COMMITTED,
            "ABORTED": TargetOutcome.FAILED,
        }
        outcome = outcomes.get(
            transaction.get("TransactionStatus"), TargetOutcome.VERIFICATION_REQUIRED
        )
        return CommitReceipt(
            outcome, "verified_transaction", receipt.load_id,
            receipt.sink_rows, receipt.transaction_id,
        )
