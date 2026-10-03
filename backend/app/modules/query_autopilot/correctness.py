"""Full, typed, bounded result equivalence, independent of latency improvements."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ResultProof:
    digest: str | None
    row_count: int
    bytes_compared: int
    types: tuple[str, ...]
    ordered: bool
    reason: str | None = None


def _typed(value: Any) -> Any:
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non_finite_value")
        return ["float", value.hex()]
    if isinstance(value, (datetime, date, time)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, (list, dict)):
        return [
            type(value).__name__,
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False),
        ]
    raise ValueError("unsupported_result_type")


def prove_result(
    rows: list[list],
    types: tuple[str, ...],
    *,
    ordered: bool,
    max_rows: int,
    max_bytes: int,
    truncated: bool = False,
) -> ResultProof:
    reason = (
        "truncated_result"
        if truncated
        else "comparison_row_budget"
        if len(rows) > max_rows
        else None
    )
    if not types and rows:
        reason = "column_types_unavailable"
    encoded: list[bytes] = []
    size = 0
    if not reason:
        try:
            for row in rows:
                if len(row) != len(types):
                    raise ValueError("column_count_changed")
                item = json.dumps(
                    [_typed(value) for value in row], separators=(",", ":"), ensure_ascii=True
                ).encode()
                size += len(item)
                if size > max_bytes:
                    raise ValueError("comparison_byte_budget")
                encoded.append(item)
        except (ValueError, TypeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "unsupported_result_type"
    if reason:
        return ResultProof(None, len(rows), size, types, ordered, reason)
    if not ordered:
        encoded.sort()
    hashed = hashlib.sha256(json.dumps(types).encode())
    for item in encoded:
        hashed.update(len(item).to_bytes(8, "big"))
        hashed.update(item)
    return ResultProof(hashed.hexdigest(), len(rows), size, types, ordered)


def equivalent(before: ResultProof, after: ResultProof) -> bool | None:
    if before.reason or after.reason:
        return None
    return (
        before.digest == after.digest
        and before.types == after.types
        and before.row_count == after.row_count
        and before.ordered == after.ordered
    )
