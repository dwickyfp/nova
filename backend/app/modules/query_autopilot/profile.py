"""Parse the pinned engine's profile counters, retaining only measured numbers."""

from __future__ import annotations

import re
from statistics import median
from typing import NotRequired, TypedDict


class ProfileOperator(TypedDict):
    operator: str
    node_id: int
    counters: dict[str, float]


class AnalyzedOperator(TypedDict):
    operator: str
    node_id: int
    estimated_rows: NotRequired[float]
    actual_rows: NotRequired[float | None]


_DURATION = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(ns|us|ms|s|m|h)")
_COUNTER = re.compile(r"^\s*-\s*([A-Za-z_]+):\s*(.+?)\s*$")
_OPERATOR = re.compile(r"^\s*([A-Z_]+) \(plan_node_id=(-?\d+)\):$")
_FE_PENDING = re.compile(r"^\s*- -- Pending\[1\] ([0-9.a-z]+)\s*$")


def milliseconds(value: str) -> float | None:
    matches = list(_DURATION.finditer(value))
    if not matches:
        return 0.0 if value == "0" else None
    if _DURATION.sub("", value).strip():
        return None
    factors = {"ns": 0.000001, "us": 0.001, "ms": 1, "s": 1000, "m": 60000, "h": 3600000}
    return sum(float(m.group(1)) * factors[m.group(2)] for m in matches)


def number(value: str) -> float | None:
    exact = re.fullmatch(r"[0-9]+(?:\.[0-9]+)?\s*[KMB]? \(([0-9]+)\)", value)
    if exact:
        return float(exact.group(1))
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", value)
    return (
        float(match.group(1)) * {"": 1, "K": 1000, "M": 1000000, "B": 1000000000}[match.group(2)]
        if match
        else None
    )


def bytes_value(value: str) -> float | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB|TB)", value)
    if not match:
        return None
    return float(match.group(1)) * 1024 ** ("B", "KB", "MB", "GB", "TB").index(match.group(2))


def profile_summary(text: str) -> dict:
    measured: dict[str, float] = {}
    operators: list[ProfileOperator] = []
    current: ProfileOperator | None = None
    scan_rows = 0
    scan_available = False
    output = None
    for line in text.splitlines():
        pending = _FE_PENDING.match(line)
        if pending:
            duration = milliseconds(pending.group(1))
            if duration is not None:
                measured["queue_ms"] = duration
            continue
        operator_match = _OPERATOR.match(line)
        if operator_match:
            current = {
                "operator": operator_match.group(1),
                "node_id": int(operator_match.group(2)),
                "counters": {},
            }
            operators.append(current)
            continue
        metric = _COUNTER.match(line)
        if not metric:
            continue
        key, raw = metric.groups()
        if key in {"QueryCumulativeCpuTime", "QueryExecutionWallTime", "QueryQueuePendingTime"}:
            value = milliseconds(raw)
            if value is not None:
                measured[
                    {
                        "QueryCumulativeCpuTime": "cpu_ms",
                        "QueryExecutionWallTime": "execution_ms",
                        "QueryQueuePendingTime": "queue_ms",
                    }[key]
                ] = value
        elif key in {"QueryPeakMemoryUsagePerNode", "QuerySpillBytes"}:
            value = bytes_value(raw)
            if value is not None:
                measured[
                    "peak_memory_bytes" if key == "QueryPeakMemoryUsagePerNode" else "spill_bytes"
                ] = value
        elif key == "RawRowsRead":
            value = number(raw)
            if value is not None:
                scan_rows += int(value)
                scan_available = True
        elif key == "NumSentRows":
            value = number(raw)
            if value is not None:
                output = int(value)
        if current is not None and key in {
            "PullRowNum",
            "PushRowNum",
            "OperatorTotalTime",
            "__MAX_OF_OperatorTotalTime",
            "__MIN_OF_OperatorTotalTime",
        }:
            value = milliseconds(raw) if key.endswith("Time") else number(raw)
            if value is not None:
                current["counters"][key] = value
    if scan_available:
        measured["scanned_rows"] = scan_rows
    if output is not None:
        measured["output_rows"] = output
    # Compare instances of the same operator; unlike unrelated operator times,
    # their spread is evidence of skew rather than different work responsibilities.
    by_node: dict[tuple[int, str], list[float]] = {}
    for operator in operators:
        value = operator["counters"].get("OperatorTotalTime")
        if value is not None:
            by_node.setdefault((operator["node_id"], operator["operator"]), []).append(value)
    spread = [values for values in by_node.values() if len(values) >= 2]
    if spread:
        values = max(spread, key=lambda v: max(v) / max(0.001, median(v)))
        measured.update(
            max_operator_ms=max(values),
            median_operator_ms=median(values),
            operator_instance_count=len(values),
        )
    return {"facts": measured, "operators": operators, "format": "starrocks-4.1.4"}


def analyzed_profile_summary(text: str, *, query_id: str) -> dict:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    identity = re.search(r"\bQueryId:\s*([0-9a-f-]+)", text, re.I)
    if identity is None or identity.group(1).lower() != query_id.lower():
        raise ValueError("analyzed_profile_identity_mismatch")
    operators: list[AnalyzedOperator] = []
    current: AnalyzedOperator | None = None
    for line in text.splitlines():
        node = re.match(r"^[│ └─├\s]*([A-Z_]+) \(id=(\d+)\)", line)
        if node:
            current = {"operator": node.group(1), "node_id": int(node.group(2))}
            operators.append(current)
        if current is None:
            continue
        estimated = re.search(r"Estimates: \[row: ([0-9.eE+-]+)", line)
        actual = re.search(r"OutputRows: ([0-9.KMB]+)(?: \((\d+)\))?", line)
        if estimated:
            current["estimated_rows"] = float(estimated.group(1))
        if actual:
            current["actual_rows"] = (
                float(actual.group(2)) if actual.group(2) else number(actual.group(1))
            )
    pairs = [
        (actual_rows, estimated_rows)
        for item in operators
        if (actual_rows := item.get("actual_rows")) is not None
        and (estimated_rows := item.get("estimated_rows")) is not None
    ]
    facts: dict[str, float] = {}
    if pairs:
        actual_rows, estimated_rows = max(
            pairs,
            key=lambda pair: max(pair[0] / max(1, pair[1]), pair[1] / max(1, pair[0])),
        )
        facts = {"estimated_rows": estimated_rows, "actual_rows": actual_rows}
    return {"facts": facts, "operators": operators, "query_id_verified": True}
