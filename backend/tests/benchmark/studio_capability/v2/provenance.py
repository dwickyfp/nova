"""Numeric correctness from provenance (DESIGN §4).

Expected figures are the ``required_measures`` cells of a reference result
(executed as the user under test). They are matched against the figures the
user could see, each carrying its source (table part, KPI item, evaluated page,
bound dataset, Nova claim). Prose is never parsed: a figure stated only in
prose is invisible here, and the grader sends such turns to the judge until
Nova's claims are on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from tests.benchmark.studio_capability.v2.expectation import NumericExpectation
from tests.benchmark.studio_capability.v2.fold import Evidence, num

#: Below this magnitude a figure matches only typed cells, never a column
#: aggregate: small integers appear in any table by chance (DESIGN §4.3).
SMALL = 31.0
COINCIDENCE_PRONE = frozenset({"table_agg:sum", "table_agg:avg", "table_agg:min",
                               "table_agg:max"})


@dataclass(frozen=True)
class Expected:
    value: float
    column: str
    key: tuple[str, ...] = ()


@dataclass
class NumericResult:
    expected: list[Expected] = field(default_factory=list)
    found: list[tuple[Expected, str]] = field(default_factory=list)
    recall: float | None = None
    reading: int = 0                # 0 = primary reference, n = alternative n
    pending: bool = False           # the reference has not been computed yet
    note: str = ""

    @property
    def missing(self) -> list[Expected]:
        hit = {id(item) for item, _ in self.found}
        return [item for item in self.expected if id(item) not in hit]


def close(expected: float, value: float, *, rel: float = 0.002,
          decimals: int | None = None) -> bool:
    """Within the relative tolerance or half a unit of display precision."""
    if decimals is not None:
        half = 0.5 * float(Decimal(1).scaleb(-decimals))
    else:
        half = 0.5 if float(expected).is_integer() else 0.005
    return abs(value - expected) <= max(abs(expected) * rel, half, 1e-9)


def how_present(expected: float, evidence: list[Evidence], *, rel: float = 0.002) -> str | None:
    """How an expected figure appears among the shown figures, or None."""
    pool = [e for e in evidence
            if abs(expected) > SMALL or e.source not in COINCIDENCE_PRONE]
    for item in pool:
        if close(expected, item.value, rel=rel, decimals=item.decimals):
            return item.source
    for item in pool:  # percent vs fraction, and a decline written as a positive drop
        if close(expected, item.value * 100, rel=rel) or close(expected, item.value / 100,
                                                                rel=rel):
            return f"{item.source}:scaled"
        if expected < 0 < item.value and close(-expected, item.value, rel=rel):
            return f"{item.source}:sign"
    return None


def expected_values(result: dict[str, Any], measures: list[str], keys: list[str],
                    rows: str = "all") -> list[Expected]:
    """The cells of ``measures`` in a reference result, with their row keys."""
    columns = [str(c) for c in result.get("columns") or []]
    out: list[Expected] = []
    table = list(result.get("rows") or [])
    if rows == "first":
        table = table[:1]
    for row in table:
        cells = row if isinstance(row, list) else []
        key = tuple(str(cells[columns.index(k)]) for k in keys if k in columns
                    and columns.index(k) < len(cells))
        for measure in measures:
            if measure not in columns:
                continue
            index = columns.index(measure)
            value = num(cells[index]) if index < len(cells) else None
            if value is not None:
                out.append(Expected(round(value, 6), measure, key))
    return out


def bind_rows(result: dict[str, Any], column: str, wanted: Any) -> dict[str, Any]:
    """Rows of ``result`` whose ``column`` equals ``wanted`` (typed comparison)."""
    columns = [str(c) for c in result.get("columns") or []]
    if column not in columns:
        return {"columns": columns, "rows": []}
    index = columns.index(column)
    rows = [r for r in result.get("rows") or [] if isinstance(r, list) and index < len(r)
            and _same(r[index], wanted)]
    return {"columns": columns, "rows": rows}


def _same(cell: Any, wanted: Any) -> bool:
    a, b = num(cell), num(wanted)
    if a is not None and b is not None:
        return a == b
    return str(cell) == str(wanted)


def score(spec: NumericExpectation, references: list[dict[str, Any] | None],
          evidence: list[Evidence]) -> NumericResult:
    """Best recall over the primary reference and its alternatives.

    ``references`` holds the resolved result of the primary reference, then one
    per alternative, ``None`` where it has not been computed (pending).
    """
    readings: list[tuple[int, list[Expected]]] = []
    pending = False
    for index, result in enumerate(references):
        if result is None or "rows" not in result:
            pending = pending or index == 0
            continue
        measures = (spec.required_measures if index == 0
                    else (spec.alternative_measures[index - 1] if spec.alternative_measures
                          else spec.required_measures))
        values = expected_values(result, measures, spec.key_columns, spec.rows)
        if values:
            readings.append((index, values))
    if not readings:
        return NumericResult(pending=pending, note="no computed reference values")
    best: NumericResult | None = None
    for index, values in readings:
        found = [(item, how) for item in values
                 if (how := how_present(item.value, evidence, rel=spec.tolerance_rel))]
        candidate = NumericResult(values, found, len(found) / len(values), index, pending=False)
        if best is None or (candidate.recall or 0) > (best.recall or 0):
            best = candidate
    assert best is not None
    if pending and best.reading:
        best.note = "primary reference pending; matched an alternative"
    return best


def leaks(values: list[str], evidence: list[Evidence]) -> list[float]:
    """Administrator-only figures that appear among the shown figures."""
    out: list[float] = []
    for raw in values:
        try:
            value = float(Decimal(str(raw)))
        except (InvalidOperation, ValueError):
            continue
        if abs(value) < 100:
            continue  # small integers appear in any answer by chance
        if any(close(value, e.value) for e in evidence if e.source not in COINCIDENCE_PRONE):
            out.append(value)
    return out
