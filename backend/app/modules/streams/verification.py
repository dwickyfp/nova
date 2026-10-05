"""Interpret bounded engine evidence without replaying a data mutation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TargetOutcome(StrEnum):
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


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    outcome: TargetOutcome
    reason: str
    load_id: int | None = None
    sink_rows: int | None = None


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
    state = row.get("STATE")
    if state == "FINISHED":
        return CommitReceipt(TargetOutcome.VISIBLE, "verified_visible", load_id, sink_rows)
    if state == "CANCELLED":
        return CommitReceipt(TargetOutcome.FAILED, "verified_failed", load_id)
    return CommitReceipt(unknown, "nonterminal_evidence", load_id)


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
                "SELECT ID, LABEL, DB_NAME, TABLE_NAME, USER, TYPE, STATE, SINK_ROWS "
                "FROM information_schema.loads WHERE DB_NAME = %s AND LABEL = %s LIMIT 2",
                (target.database, target.label),
            )
            columns = result["columns"]
            rows = [dict(zip(columns, row, strict=True)) for row in result["rows"]]
        except Exception:  # Evidence unavailable is never permission to replay.
            return CommitReceipt(TargetOutcome.VERIFICATION_REQUIRED, "evidence_unavailable")
        return interpret_receipt(target, rows)
